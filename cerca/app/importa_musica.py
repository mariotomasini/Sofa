"""Portare in casa le playlist di Apple Music (o di altri servizi).

1. Si esportano le playlist in CSV con TuneMyMusic o Soundiiz (Apple non lo permette
   dall'iPhone) e si caricano nella pagina Musica → Importa.
2. Per ogni brano: se è già nella musica di casa (Navidrome) va bene così; se no si cerca
   su Deezer e si scarica come con il tasto Scarica, pochi alla volta.
3. Man mano che i brani sono pronti si mettono nella playlist con lo stesso nome, con
   l'utente Navidrome di chi ha importato; i "Brani preferiti" diventano preferiti.
4. A importazione finita, un avviso solo (non uno per brano).
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import re
import time
from pathlib import Path

from .archive import normalizza
from .download import FONTE_IMPORTAZIONE
from .navidrome import Navidrome, credenziali
from .rete import ErroreServizio

log = logging.getLogger("cerca.importa_musica")

CHIAVE = "importa_musica:"
OGNI = 60
MAX_IN_DOWNLOAD = 3  # brani scaricati insieme, tra tutte le importazioni: non si intasano indexer e qBittorrent
MAX_CONTROLLI_PER_GIRO = 40  # brani nuovi controllati (Navidrome, Deezer) in un giro
ORE_SCANSIONE = 3  # scaricato ma Navidrome non lo trova ancora: dopo queste ore si lascia perdere
MAX_BRANI = 5000

COLONNE = {
    "titolo": ("track name", "track", "title", "song", "song name", "name", "titolo", "brano", "nome"),
    "artista": ("artist name", "artist", "artists", "artist name(s)", "artista", "artisti"),
    "album": ("album", "album name", "album title"),
    "playlist": ("playlist name", "playlist", "playlist title"),
}
PREFERITI = {"brani preferiti", "favourite songs", "favorite songs", "loved", "loved songs", "preferiti",
             "liked songs", "mi piace", "brani che mi piacciono"}
LIBRERIA = {"library", "libreria", "songs", "brani", "my library", "la mia libreria", "tutti i brani", "music"}
ATTIVI = ("attesa", "download", "scaricato")


def _colonna(intestazioni: list[str], tipo: str) -> int | None:
    pulite = [re.sub(r"\s+", " ", (h or "").strip().lstrip("﻿").lower()) for h in intestazioni]
    for nome in COLONNE[tipo]:
        if nome in pulite:
            return pulite.index(nome)
    return None


def leggi_csv(testo: str, nome_file: str = "") -> dict[str, list[dict]]:
    """{nome playlist: [{"titolo", "artista", "album"}]} da un CSV di TuneMyMusic, Soundiiz e simili."""
    testo = testo.lstrip("﻿")
    righe_prova = "\n".join(testo.splitlines()[:5])
    try:
        dialetto = csv.Sniffer().sniff(righe_prova, delimiters=",;\t")
    except csv.Error:
        dialetto = csv.excel
    righe = list(csv.reader(io.StringIO(testo), dialetto))
    if not righe:
        raise ErroreServizio(f"Il file \"{nome_file}\" è vuoto.")
    intestazioni = righe[0]
    c_titolo, c_artista = _colonna(intestazioni, "titolo"), _colonna(intestazioni, "artista")
    c_album, c_playlist = _colonna(intestazioni, "album"), _colonna(intestazioni, "playlist")
    if c_titolo is None or c_artista is None:
        raise ErroreServizio(
            f"Nel file \"{nome_file}\" non trovo le colonne del titolo e dell'artista.",
            "Esporta le playlist in formato CSV da TuneMyMusic o Soundiiz e carica quel file così com'è.")
    predefinita = Path(nome_file).stem.strip() or "Playlist importata"

    def campo(riga: list[str], c: int | None) -> str:
        return riga[c].strip() if c is not None and c < len(riga) else ""

    playlist: dict[str, list[dict]] = {}
    for riga in righe[1:]:
        titolo, artista = campo(riga, c_titolo), campo(riga, c_artista)
        if not titolo or not artista:
            continue
        # "Artista1, Artista2": per cercare basta il primo
        artista = re.split(r"\s*(?:,|;|&| feat\.? | ft\.? )\s*", artista)[0] or artista
        nome = campo(riga, c_playlist) or predefinita
        playlist.setdefault(nome, []).append({"titolo": titolo, "artista": artista, "album": campo(riga, c_album)})
    if not playlist:
        raise ErroreServizio(f"Nel file \"{nome_file}\" non ci sono brani.")
    return playlist


def tipo_playlist(nome: str) -> str:
    """"preferiti" (diventano preferiti), "libreria" (solo da scaricare) o "playlist"."""
    n = normalizza(nome)
    if n in {normalizza(x) for x in PREFERITI}:
        return "preferiti"
    if n in {normalizza(x) for x in LIBRERIA}:
        return "libreria"
    return "playlist"


def chiave_brano(artista: str, titolo: str) -> str:
    return f"{normalizza(artista)}|{normalizza(titolo)}"


def riassunto(lavoro: dict) -> dict:
    conta = {s: 0 for s in ("pronto", "non_trovato", *ATTIVI)}
    for b in lavoro["brani"]:
        conta[b["stato"]] = conta.get(b["stato"], 0) + 1
    return {
        "id": lavoro["id"], "nome": lavoro["nome"], "tipo": lavoro["tipo"], "utente": lavoro["utente"]["nome"],
        "creato": lavoro["creato"], "totale": len(lavoro["brani"]), "pronti": conta["pronto"],
        "in_corso": sum(conta[s] for s in ATTIVI), "non_trovati": conta["non_trovato"],
        "finito": not any(conta[s] for s in ATTIVI), "errore": lavoro.get("errore", ""),
        "elenco_non_trovati": [{"artista": b["artista"], "titolo": b["titolo"], "motivo": b.get("motivo", "")}
                               for b in lavoro["brani"] if b["stato"] == "non_trovato"],
    }


class ImportaMusica:
    def __init__(self, config, archivio, client, deezer, scarica_musica, notifica=None) -> None:
        self.config = config
        self.archivio = archivio
        self.client = client
        self.deezer = deezer
        self.scarica = scarica_musica
        self.notifica = notifica
        self._blocco = asyncio.Lock()
        self._ultima_scansione = 0.0
        # download finiti e già tolti dall'elenco: {id: None se riuscito, se no il motivo}.
        # Serve quando lo stesso brano è in due importazioni (es. di due persone).
        self._esiti: dict[int, str | None] = {}

    @property
    def attivo(self) -> bool:
        return bool(self.config.navidrome_url)

    # --- archivio ---

    def _lavori(self) -> list[dict]:
        lavori = []
        for valore in self.archivio.stato_con_prefisso(CHIAVE).values():
            try:
                lavori.append(json.loads(valore))
            except ValueError:
                continue
        return sorted(lavori, key=lambda x: x.get("creato", 0))

    def _salva(self, lavoro: dict) -> None:
        self.archivio.stato_scrivi(f"{CHIAVE}{lavoro['id']}", json.dumps(lavoro))

    def elenco(self, utente: dict | None) -> list[dict]:
        """Le importazioni di questa persona (l'amministratore le vede tutte)."""
        tutti = not utente or utente.get("admin")
        return [riassunto(x) for x in reversed(self._lavori())
                if tutti or x["utente"].get("id") == (utente or {}).get("id")]

    def nuova(self, utente: dict | None, file: list[tuple[str, str]]) -> list[dict]:
        """file: [(nome, testo CSV)]. Una importazione per ogni playlist trovata."""
        if not self.attivo:
            raise ErroreServizio("Navidrome è spento.",
                                 "Accendilo dalle impostazioni (Musica nelle app per telefono): le playlist vanno lì.")
        playlist: dict[str, list[dict]] = {}
        for nome, testo in file:
            for nome_pl, brani in leggi_csv(testo, nome).items():
                playlist.setdefault(nome_pl, []).extend(brani)
        if sum(len(b) for b in playlist.values()) > MAX_BRANI:
            raise ErroreServizio(f"Sono più di {MAX_BRANI} brani.", "Carica le playlist poche alla volta.")
        return [self.nuova_da_brani(utente, nome_pl, brani, indice=i) for i, (nome_pl, brani) in enumerate(playlist.items())]

    def nuova_da_brani(self, utente: dict | None, nome: str, brani: list[dict], origine: str = "importazione",
                       indice: int = 0) -> dict:
        """Una playlist da portare in Navidrome. brani: [{"titolo", "artista", "album", "id_deezer"?}].
        origine "scoperte": la playlist automatica della settimana (cambia solo l'avviso)."""
        chi = {"id": str((utente or {}).get("id") or "admin"), "nome": (utente or {}).get("nome") or self.config.utente,
               "admin": bool(not utente or utente.get("admin"))}
        visti, unici = set(), []
        for b in brani:  # lo stesso brano due volte nella stessa playlist: una volta sola
            k = chiave_brano(b["artista"], b["titolo"])
            if k not in visti:
                visti.add(k)
                unici.append({**b, "stato": "attesa"})
        lavoro = {"id": f"{int(time.time() * 1000)}{indice}", "nome": nome,
                  "tipo": "playlist" if origine == "scoperte" else tipo_playlist(nome), "origine": origine,
                  "utente": chi, "creato": time.time(), "brani": unici, "id_playlist": "", "avvisato": False}
        self._salva(lavoro)
        return riassunto(lavoro)

    def elimina(self, id_lavoro: str, utente: dict | None) -> None:
        """Smette di importare (i brani già scaricati e la playlist in Navidrome restano)."""
        for lavoro in self._lavori():
            if lavoro["id"] == id_lavoro and (not utente or utente.get("admin")
                                               or lavoro["utente"].get("id") == utente.get("id")):
                self.archivio.stato_togli(f"{CHIAVE}{id_lavoro}")
                return
        raise ErroreServizio("Non trovo questa importazione.", "Ricarica la pagina.")

    def riprova(self, id_lavoro: str, utente: dict | None) -> int:
        for lavoro in self._lavori():
            if lavoro["id"] == id_lavoro and (not utente or utente.get("admin")
                                               or lavoro["utente"].get("id") == utente.get("id")):
                quanti = 0
                for b in lavoro["brani"]:
                    if b["stato"] == "non_trovato":
                        b.update(stato="attesa", motivo="")
                        quanti += 1
                lavoro["avvisato"] = False
                self._salva(lavoro)
                return quanti
        raise ErroreServizio("Non trovo questa importazione.", "Ricarica la pagina.")

    # --- il lavoro di sottofondo ---

    def _navidrome(self, utente: dict | None) -> Navidrome:
        nome, password = credenziali(self.config, utente)
        return Navidrome(self.config, self.client, nome, password)

    async def _trova_su_deezer(self, artista: str, titolo: str) -> int | None:
        for domanda in (f'artist:"{artista}" track:"{titolo}"', f"{artista} {titolo}"):
            dati = await self.deezer._get("search/track", {"q": domanda, "limit": 10})
            for t in dati.get("data") or []:
                nome_artista = normalizza((t.get("artist") or {}).get("name", ""))
                a = normalizza(artista)
                titolo_dz = normalizza(t.get("title_short") or t.get("title") or "")
                if (a in nome_artista or nome_artista in a) and (
                        titolo_dz == normalizza(titolo) or titolo_dz.startswith(normalizza(titolo))):
                    return t.get("id")
        return None

    async def _controlla_nuovo(self, b: dict, admin: Navidrome, in_corso: dict[str, int], liberi: int) -> int:
        """Un brano in attesa: già in casa, in download da un'altra importazione, o da scaricare."""
        id_nd = await admin.cerca_brano(b["artista"], b["titolo"])
        if id_nd:
            b.update(stato="pronto", id_nd=id_nd)
            return liberi
        k = chiave_brano(b["artista"], b["titolo"])
        if k in in_corso:
            b.update(stato="download", id_download=in_corso[k], dal=time.time())
            return liberi
        if liberi <= 0:
            return liberi
        id_deezer = b.get("id_deezer") or await self._trova_su_deezer(b["artista"], b["titolo"])
        if not id_deezer:
            b.update(stato="non_trovato", motivo="Non lo trovo su Deezer: controlla titolo e artista.")
            return liberi
        try:
            esito = await self.scarica.brano(id_deezer, fonte=FONTE_IMPORTAZIONE)
        except ErroreServizio as exc:
            b.update(stato="non_trovato", motivo=exc.cosa)
            return liberi
        b.update(stato="download", id_download=esito["id"], dal=time.time())
        in_corso[k] = esito["id"]
        return liberi - 1

    async def _segui_download(self, b: dict, admin: Navidrome) -> bool:
        """Un brano in download o già scaricato. True se serve una scansione di Navidrome."""
        if b["stato"] == "download":
            voce = self.archivio.download(b["id_download"])
            if voce is None and b["id_download"] not in self._esiti:
                b.update(stato="non_trovato", motivo="Il download è stato annullato.")
                return False
            if voce is not None and voce["stato"] == "errore":
                self._esiti[voce["id"]] = voce.get("messaggio") or "Il download non è riuscito."
                self.archivio.elimina_download(voce["id"])  # il motivo resta nell'importazione
            elif voce is not None and voce["stato"] == "importato":
                self._esiti[voce["id"]] = None
                self.archivio.elimina_download(voce["id"])  # la pagina Download non si riempie di brani
            elif voce is not None:
                return False
            motivo = self._esiti[b["id_download"]]
            if motivo:
                b.update(stato="non_trovato", motivo=motivo)
                return False
            b.update(stato="scaricato", dal=time.time())
        id_nd = await admin.cerca_brano(b["artista"], b["titolo"])
        if id_nd:
            b.update(stato="pronto", id_nd=id_nd)
            return False
        if time.time() - b.get("dal", 0) > ORE_SCANSIONE * 3600:
            b.update(stato="non_trovato", motivo="Scaricato, ma Navidrome non lo riconosce (titolo diverso nel file).")
            return False
        return True

    async def _metti_in_navidrome(self, lavoro: dict) -> None:
        nuovi = [b for b in lavoro["brani"] if b["stato"] == "pronto" and not b.get("messo") and b.get("id_nd")]
        if not nuovi or lavoro["tipo"] == "libreria":
            for b in nuovi:
                b["messo"] = True
            return
        nd = self._navidrome(lavoro["utente"])
        ids = list(dict.fromkeys(b["id_nd"] for b in nuovi))
        if lavoro["tipo"] == "preferiti":
            await nd.preferiti(ids)
        else:
            if not lavoro.get("id_playlist"):
                lavoro["id_playlist"] = await nd.playlist(lavoro["nome"]) or ""
            if lavoro["id_playlist"]:
                await nd.aggiungi(lavoro["id_playlist"], ids)
            else:
                lavoro["id_playlist"] = await nd.crea_playlist(lavoro["nome"], ids)
        for b in nuovi:
            b["messo"] = True

    async def _avvisa_se_finito(self, lavoro: dict) -> None:
        r = riassunto(lavoro)
        if not r["finito"] or lavoro.get("avvisato"):
            return
        lavoro["avvisato"] = True
        cosa = {"preferiti": "nei preferiti", "libreria": "nella musica di casa"}.get(lavoro["tipo"], "nella playlist")
        brani = "1 brano" if r["pronti"] == 1 else f"{r['pronti']} brani"
        messaggio = f"\"{lavoro['nome']}\": {brani} {cosa}"
        if r["non_trovati"]:
            messaggio += ", 1 non trovato." if r["non_trovati"] == 1 else f", {r['non_trovati']} non trovati."
        else:
            messaggio += "."
        titolo, dove = "Musica importata", "/musica/importa"
        if lavoro.get("origine") == "scoperte":
            titolo, dove = "Le tue scoperte sono pronte", f"/musica/playlist/{lavoro.get('id_playlist', '')}"
            messaggio = f"{brani} nuovi da ascoltare, scelti in base a quello che ti piace."
        log.info("%s: %s", titolo, messaggio)
        if self.notifica:
            try:
                await self.notifica(titolo, messaggio, f"{self.config.indirizzo_pubblico}{self.config.root_path}{dove}")
            except Exception:  # noqa: BLE001
                log.warning("Avviso dell'importazione non inviato", exc_info=True)

    async def giro(self) -> None:
        if not self.attivo:
            return
        async with self._blocco:
            lavori = [x for x in self._lavori() if not riassunto(x)["finito"] or not x.get("avvisato")
                      or any(b["stato"] == "pronto" and not b.get("messo") for b in x["brani"])]
            if not lavori:
                return
            admin = self._navidrome(None)
            in_corso = {chiave_brano(b["artista"], b["titolo"]): b["id_download"]
                        for x in self._lavori() for b in x["brani"] if b["stato"] == "download"}
            liberi = MAX_IN_DOWNLOAD - len(set(in_corso.values()))
            controlli = MAX_CONTROLLI_PER_GIRO
            serve_scansione = False
            for lavoro in lavori:
                try:
                    for b in lavoro["brani"]:
                        if b["stato"] in ("download", "scaricato"):
                            serve_scansione |= await self._segui_download(b, admin)
                    for b in lavoro["brani"]:
                        if b["stato"] == "attesa" and controlli > 0:
                            controlli -= 1
                            liberi = await self._controlla_nuovo(b, admin, in_corso, liberi)
                    await self._metti_in_navidrome(lavoro)
                    lavoro["errore"] = ""
                    await self._avvisa_se_finito(lavoro)
                except ErroreServizio as exc:
                    lavoro["errore"] = f"{exc.cosa} {exc.cosa_fare}".strip()
                    log.warning("Importazione \"%s\": %s", lavoro["nome"], lavoro["errore"])
                finally:
                    if self.archivio.stato_leggi(f"{CHIAVE}{lavoro['id']}") is not None:  # non eliminata nel frattempo
                        self._salva(lavoro)
            if serve_scansione and time.time() - self._ultima_scansione > 600:
                self._ultima_scansione = time.time()
                try:
                    await admin.scansiona()
                except ErroreServizio:
                    log.warning("Scansione di Navidrome non avviata", exc_info=True)

    async def ciclo(self) -> None:
        while True:
            try:
                await self.giro()
            except Exception:  # noqa: BLE001 - un errore non deve fermare le importazioni per sempre
                log.exception("Errore nell'importazione della musica")
            await asyncio.sleep(OGNI)
