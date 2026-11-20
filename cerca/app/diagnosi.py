"""Controllo servizi: chi risponde, chi no e cosa fare.

Serve a capire in pochi secondi perché una pagina di Cerca non si carica:
ogni servizio viene provato con un tempo massimo breve, tutti insieme.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass

import httpx

from .config import Config
from .jellyfin import INTESTAZIONE
from .rete import AGENTE

TEMPO = 6  # secondi massimi per ogni servizio
LENTO = 3  # oltre questi secondi il servizio risponde, ma è lento


@dataclass
class Risultato:
    nome: str
    a_cosa_serve: str
    stato: str  # ok | lento | errore
    millisecondi: int = 0
    messaggio: str = ""
    cosa_fare: str = ""


def _comando(servizio: str) -> str:
    return (f"Sul PC scrivi \"mediabox logs {servizio}\" per vedere i suoi messaggi, poi \"mediabox restart {servizio}\". "
            "Sui PC con poca memoria spegni dalle impostazioni i programmi che non usi.")


async def _prova(client: httpx.AsyncClient, nome: str, a_cosa_serve: str, url: str, *, servizio: str = "",
                 intestazioni: dict | None = None, esterno: bool = False) -> Risultato:
    inizio = time.monotonic()
    try:
        r = await client.get(url, headers=intestazioni or {}, timeout=httpx.Timeout(TEMPO, connect=3))
    except httpx.TimeoutException:
        return Risultato(nome, a_cosa_serve, "errore", TEMPO * 1000, "Non risponde in tempo.",
                         "Controlla la connessione a Internet." if esterno else _comando(servizio))
    except httpx.HTTPError as exc:
        return Risultato(nome, a_cosa_serve, "errore", 0, f"Spento o irraggiungibile ({type(exc).__name__}).",
                         "Controlla la connessione a Internet." if esterno else _comando(servizio))
    ms = int((time.monotonic() - inizio) * 1000)
    if r.status_code == 401:
        if esterno:
            return Risultato(nome, a_cosa_serve, "errore", ms, "Non accetta la chiave.",
                             "Controlla la chiave nelle impostazioni di mediabox (/impostazioni), oppure toglila.")
        return Risultato(nome, a_cosa_serve, "errore", ms, "Acceso, ma non accetta la chiave di mediabox.",
                         f"Sul PC scrivi \"mediabox restart {servizio}\" e poi \"mediabox configura\".")
    if r.status_code >= 500:
        return Risultato(nome, a_cosa_serve, "errore", ms, f"Acceso, ma risponde con un errore ({r.status_code}).",
                         _comando(servizio))
    stato = "lento" if ms > LENTO * 1000 else "ok"
    return Risultato(nome, a_cosa_serve, stato, ms, "Risponde, ma lentamente." if stato == "lento" else "Risponde.")


async def _jellyfin(client: httpx.AsyncClient, config: Config) -> Risultato:
    base = await _prova(client, "Jellyfin", "guardare i film e \"Continua a guardare\"",
                        f"{config.jellyfin_url}/System/Info/Public", servizio="jellyfin")
    if base.stato == "errore":
        return base
    try:
        r = await client.post(
            f"{config.jellyfin_url}/Users/AuthenticateByName",
            json={"Username": config.utente, "Pw": config.password},
            headers={"Authorization": INTESTAZIONE}, timeout=TEMPO,
        )
    except httpx.HTTPError:
        return base
    if r.status_code == 401:
        base.stato = "errore"
        base.messaggio = f"Acceso, ma l'utente \"{config.utente}\" con la password di mediabox non entra."
        base.cosa_fare = ("Se Jellyfin è stato configurato a mano con un altro utente, sul PC scrivi "
                          "\"mediabox azzera-jellyfin\". Se la configurazione non era ancora arrivata a Jellyfin, "
                          "scrivi \"mediabox configura\".")
    return base


async def controlla(config: Config, client: httpx.AsyncClient) -> dict:
    prove = [
        _prova(client, "Radarr", "film: ricerca e schede (senza chiave TMDB), download e importazione",
               f"{config.radarr_url}/api/v3/system/status", servizio="radarr",
               intestazioni={"X-Api-Key": config.radarr_chiave}),
        _prova(client, "qBittorrent", "scaricare i torrent", f"{config.qbit_url}/api/v2/app/version",
               servizio="qbittorrent"),
        _prova(client, "Prowlarr", "cercare i torrent sugli indexer", f"{config.prowlarr_url}/api/v1/system/status",
               servizio="prowlarr", intestazioni={"X-Api-Key": config.prowlarr_chiave}),
        _jellyfin(client, config),
    ]
    if config.sonarr_url:
        prove.append(_prova(client, "Sonarr", "serie TV", f"{config.sonarr_url}/api/v3/system/status",
                            servizio="sonarr", intestazioni={"X-Api-Key": config.sonarr_chiave}))
    if config.lidarr_url:
        prove.append(_prova(client, "Lidarr", "musica da scaricare", f"{config.lidarr_url}/api/v1/system/status",
                            servizio="lidarr", intestazioni={"X-Api-Key": config.lidarr_chiave}))
    esterni = {"User-Agent": AGENTE}
    if config.con_tmdb:
        lunga = len(config.tmdb_chiave) > 40  # il token lungo va nell'intestazione, la chiave corta nell'indirizzo
        prove.append(_prova(
            client, "TMDB", "catalogo dei film e dove vederli",
            f"{config.tmdb_api}/configuration" + ("" if lunga else f"?api_key={config.tmdb_chiave}"), esterno=True,
            intestazioni={**esterni, **({"Authorization": f"Bearer {config.tmdb_chiave}"} if lunga else {})},
        ))
    else:
        prove.append(_prova(client, "Wikidata", "generi, cartoni e originali delle piattaforme",
                            "https://query.wikidata.org/sparql?query=ASK%7B%7D&format=json", esterno=True,
                            intestazioni=esterni))
        prove.append(_prova(client, "TVmaze", "serie TV del momento", "https://api.tvmaze.com/shows/1",
                            esterno=True, intestazioni=esterni))
    prove.append(_prova(client, "Internet Archive", "film di pubblico dominio",
                        "https://archive.org/advancedsearch.php?q=test&rows=1&output=json", esterno=True,
                        intestazioni=esterni))
    risultati = await asyncio.gather(*prove)
    return {
        "risultati": [asdict(r) for r in risultati],
        "tutto_ok": all(r.stato == "ok" for r in risultati),
        "con_tmdb": config.con_tmdb,
    }
