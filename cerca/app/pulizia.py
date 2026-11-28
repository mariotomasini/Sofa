"""Pulizia automatica quando il disco dei media sta per riempirsi.

Due livelli, dal più sicuro:
1. File abbandonati nei download (sempre): residui di download falliti o
   annullati, che nessun torrent e nessun download di Cerca usa più, fermi da
   almeno GIORNI_ABBANDONO giorni.
2. Film ed episodi già visti (solo con PULIZIA_VISTI=si): visti da qualcuno da
   più di PULIZIA_GIORNI giorni, non preferiti, non a metà. Si cancellano da
   Radarr/Sonarr (che smettono di seguirli, così non vengono riscaricati),
   partendo dai visti da più tempo, finché lo spazio torna sopra la soglia.

Non si tocca mai: la musica, le registrazioni, i film copiati a mano (che
Radarr non conosce), i preferiti, quello che qualcuno sta ancora guardando.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import re
import shutil
import time
from pathlib import Path

import httpx

from .config import Config
from .db import Archivio
from .foto_protette import controlla as mai_le_foto
from .rete import ErroreServizio
from .stato import spazio

log = logging.getLogger("cerca.pulizia")

GIORNI_ABBANDONO = 14
MARGINE = 5  # si libera fino a soglia + 5% per non ripartire subito


# --- funzioni pure (testate) ---------------------------------------------------------

def dimensione(percorso: Path) -> int:
    if percorso.is_file():
        return percorso.stat().st_size
    totale = 0
    for radice, _, file in os.walk(percorso):
        for f in file:
            try:
                totale += os.path.getsize(os.path.join(radice, f))
            except OSError:
                pass
    return totale


def ultima_modifica(percorso: Path) -> float:
    if percorso.is_file():
        return percorso.stat().st_mtime
    ultima = percorso.stat().st_mtime
    for radice, cartelle, file in os.walk(percorso):
        for nome in cartelle + file:
            try:
                ultima = max(ultima, os.path.getmtime(os.path.join(radice, nome)))
            except OSError:
                pass
    return ultima


def _dimensione_file(p: Path) -> int:
    return p.stat().st_size if p.exists() else 0


def _cancella(percorso: Path) -> None:
    mai_le_foto(percorso)  # foto e video di Immich: mai, qualunque cosa succeda
    # un collegamento si toglie da solo: is_dir() lo segue e rmtree() rifiuta i collegamenti,
    # così un collegamento a una cartella restava per sempre tra gli "abbandonati"
    if percorso.is_dir() and not percorso.is_symlink():
        shutil.rmtree(percorso)
    else:
        percorso.unlink()


def protetto(percorso: Path, in_uso: set[str]) -> bool:
    """Vero se il percorso (o qualcosa al suo interno) è usato da un download."""
    p = str(percorso).rstrip("/")
    return any(u == p or u.startswith(p + "/") or p.startswith(u + "/") for u in in_uso)


def trova_abbandonati(
    cartella_download: Path, in_uso: set[str], adesso: float, giorni: int = GIORNI_ABBANDONO
) -> list[dict]:
    risultato = []
    if not cartella_download.exists():
        return risultato
    for categoria in sorted(cartella_download.iterdir()):
        elementi = sorted(categoria.iterdir()) if categoria.is_dir() else [categoria]
        for elemento in elementi:
            if protetto(elemento, in_uso):
                continue
            try:
                eta = adesso - ultima_modifica(elemento)
            except OSError:
                continue
            if eta < giorni * 86400:
                continue
            risultato.append({
                "tipo": "abbandonato",
                "percorso": str(elemento),
                "nome": elemento.name,
                "dimensione": dimensione(elemento),
                "giorni": int(eta // 86400),
            })
    return risultato


def scegli_visti(elementi: dict[str, dict], adesso: dt.datetime, giorni: int) -> list[dict]:
    """Dai dati di Jellyfin (già uniti per tutti gli utenti) i candidati, dal visto da più tempo."""
    candidati = []
    for e in elementi.values():
        if not e["visto"] or e["preferito"] or e["a_meta"] or not e["ultima_visione"]:
            continue
        if adesso - e["ultima_visione"] < dt.timedelta(days=giorni):
            continue
        candidati.append(e)
    return sorted(candidati, key=lambda e: e["ultima_visione"])


def leggi_data(testo: str | None) -> dt.datetime | None:
    """Date di Jellyfin come "2026-11-19T10:00:00.1234567Z" (7 decimali, troppi per Python)."""
    if not testo:
        return None
    testo = re.sub(r"(\.\d{6})\d+", r"\1", testo.strip()).replace("Z", "+00:00")
    try:
        data = dt.datetime.fromisoformat(testo)
    except ValueError:
        return None
    return data if data.tzinfo else data.replace(tzinfo=dt.UTC)


def unisci_dati_utente(elementi: dict[str, dict], voce: dict) -> None:
    """Somma i dati di visione di un utente a quelli già raccolti."""
    dati = voce.get("UserData") or {}
    e = elementi.setdefault(voce["Id"], {
        "id": voce["Id"],
        "tipo": "film" if voce.get("Type") == "Movie" else "episodio",
        "nome": voce.get("Name", ""),
        "serie": voce.get("SeriesName", ""),
        "percorso": voce.get("Path", ""),
        "tmdb": (voce.get("ProviderIds") or {}).get("Tmdb", ""),
        "visto": False, "preferito": False, "a_meta": False, "ultima_visione": None,
    })
    e["visto"] |= bool(dati.get("Played"))
    e["preferito"] |= bool(dati.get("IsFavorite"))
    e["a_meta"] |= bool(dati.get("PlaybackPositionTicks")) and not dati.get("Played")
    data = leggi_data(dati.get("LastPlayedDate"))
    if data and (not e["ultima_visione"] or data > e["ultima_visione"]):
        e["ultima_visione"] = data


def percorso_radarr(percorso_jellyfin: str) -> str:
    """Jellyfin vede /data/film, Radarr e Cerca /data/media/film."""
    if percorso_jellyfin.startswith("/data/"):
        return "/data/media/" + percorso_jellyfin[len("/data/"):]
    return percorso_jellyfin


# --- gestore ---------------------------------------------------------------------------

class Pulizia:
    def __init__(self, config: Config, archivio: Archivio, client: httpx.AsyncClient, qbit, jellyfin, avvisami) -> None:
        self.config = config
        self.archivio = archivio
        self.client = client
        self.qbit = qbit
        self.jellyfin = jellyfin
        self.avvisami = avvisami

    # --- cosa si può togliere ----------------------------------------------------------
    async def _in_uso(self) -> set[str]:
        in_uso: set[str] = set()
        try:
            torrent = await self.qbit.tutti()
        except ErroreServizio:
            # qBittorrent spento: per sicurezza non si cancella nessun download
            raise ErroreServizio("qBittorrent non risponde: per sicurezza non pulisco i download.") from None
        for t in torrent:
            if t.get("content_path"):
                in_uso.add(t["content_path"].rstrip("/"))
        for d in self.archivio.elenco_download(500):
            if d["stato"] != "importato" and d.get("percorso"):
                in_uso.add(d["percorso"].rstrip("/"))
                in_uso.add(d["percorso"] + ".part")
        return in_uso

    async def abbandonati(self) -> list[dict]:
        # leggere le cartelle sul disco dei film può essere lento (su Windows molto): in un thread a parte
        in_uso = await self._in_uso()
        return await asyncio.to_thread(trova_abbandonati, self.config.cartella_download, in_uso, time.time())

    async def visti(self) -> list[dict]:
        if not self.config.pulizia_visti:
            return []
        elementi: dict[str, dict] = {}
        for utente in await self.jellyfin.utenti():
            for voce in await self.jellyfin.elementi_utente(utente):
                unisci_dati_utente(elementi, voce)
        scelti = scegli_visti(elementi, dt.datetime.now(dt.UTC), self.config.pulizia_giorni)
        for e in scelti:
            e["dimensione"] = await asyncio.to_thread(_dimensione_file, Path(percorso_radarr(e["percorso"])))
            e["giorni"] = (dt.datetime.now(dt.UTC) - e["ultima_visione"]).days
            e["ultima_visione"] = e["ultima_visione"].isoformat()
        return [e for e in scelti if e["dimensione"]]

    async def anteprima(self) -> dict:
        abbandonati = await self.abbandonati()
        visti = await self.visti()
        return {
            "abbandonati": abbandonati,
            "visti": visti,
            "pulizia_visti": self.config.pulizia_visti,
            "giorni": self.config.pulizia_giorni,
            "totale": sum(x["dimensione"] for x in abbandonati + visti),
            "registro": self.archivio.pulizie(30),
        }

    # --- cancellazione ---------------------------------------------------------------------
    async def _arr(self, metodo: str, base: str, chiave: str, percorso: str, **kw):
        r = await self.client.request(metodo, f"{base}/api/v3/{percorso}", headers={"X-Api-Key": chiave}, timeout=60, **kw)
        r.raise_for_status()
        return r.json() if r.content else None

    async def _togli_film(self, e: dict) -> bool:
        if not e["tmdb"]:
            return False
        base, chiave = self.config.radarr_url, self.config.radarr_chiave
        film = await self._arr("GET", base, chiave, "movie", params={"tmdbId": e["tmdb"]})
        if not film or not film[0].get("movieFile"):
            return False  # Radarr non lo gestisce (film copiato a mano): non si tocca
        film = film[0]
        film["monitored"] = False  # così Radarr non lo riscarica
        await self._arr("PUT", base, chiave, f"movie/{film['id']}", json=film)
        await self._arr("DELETE", base, chiave, f"moviefile/{film['movieFile']['id']}")
        return True

    async def _togli_episodio(self, e: dict) -> bool:
        if not self.config.sonarr_url:
            return False
        base, chiave = self.config.sonarr_url, self.config.sonarr_chiave
        percorso = percorso_radarr(e["percorso"])
        serie = await self._arr("GET", base, chiave, "series")
        della_serie = next((s for s in serie if percorso.startswith(s.get("path", "").rstrip("/") + "/")), None)
        if not della_serie:
            return False
        file = await self._arr("GET", base, chiave, "episodefile", params={"seriesId": della_serie["id"]})
        voce = next((f for f in file if f.get("path") == percorso), None)
        if not voce:
            return False
        episodi = await self._arr("GET", base, chiave, "episode", params={"episodeFileId": voce["id"]})
        if episodi:
            await self._arr("PUT", base, chiave, "episode/monitor",
                            json={"episodeIds": [x["id"] for x in episodi], "monitored": False})
        await self._arr("DELETE", base, chiave, f"episodefile/{voce['id']}")
        return True

    async def _libero_percento(self) -> float:
        return (await asyncio.to_thread(spazio, self.config.cartella_download.parent))["libero_percento"]

    async def libera(self, motivo: str) -> dict:
        """Libera spazio fino a soglia + margine. Restituisce cosa è stato tolto."""
        obiettivo = self.config.soglia_disco + MARGINE
        tolti: list[dict] = []
        for a in await self.abbandonati():
            percorso = Path(a["percorso"])
            try:
                await asyncio.to_thread(_cancella, percorso)
            except OSError as exc:
                log.warning("Non riesco a cancellare %s: %s", percorso, exc)
                continue
            tolti.append(a)
            self.archivio.registra_pulizia(f"Download abbandonato: {a['nome']}", a["dimensione"], motivo)
        if await self._libero_percento() < obiettivo:
            for e in await self.visti():
                if await self._libero_percento() >= obiettivo:
                    break
                try:
                    fatto = await (self._togli_film(e) if e["tipo"] == "film" else self._togli_episodio(e))
                except httpx.HTTPError as exc:
                    log.warning("Non riesco a togliere %s: %s", e["nome"], exc)
                    continue
                if fatto:
                    nome = e["nome"] if e["tipo"] == "film" else f"{e['serie']} – {e['nome']}"
                    tolti.append(e)
                    self.archivio.registra_pulizia(
                        f"{'Film' if e['tipo'] == 'film' else 'Episodio'} visto {e['giorni']} giorni fa: {nome}",
                        e["dimensione"], motivo)
        liberati = sum(t["dimensione"] for t in tolti)
        if tolti:
            await self.jellyfin.aggiorna_libreria()
            await self.avvisami.notifica(
                "Pulizia del disco",
                f"Ho liberato {liberati / 1024**3:.1f} GB togliendo {len(tolti)} elementi "
                f"(il dettaglio è nella pagina Spazio di Cerca).",
                f"{self.config.indirizzo_pubblico}{self.config.root_path}/spazio",
            )
        return {"tolti": len(tolti), "liberati": liberati, "libero_percento": await self._libero_percento()}
