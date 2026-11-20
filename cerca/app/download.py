"""Gestione dei download: HTTP diretto da Internet Archive, torrent via
qBittorrent, e passaggio finale a Radarr (che mette il film in Jellyfin)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
import unicodedata
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from .config import Config
from .db import Archivio
from .jellyfin import Jellyfin
from .qbittorrent import STATI_ERRORE, STATI_FINITI, QBittorrent
from .radarr import Radarr
from .rete import ErroreServizio
from .tmdb import TMDB

log = logging.getLogger("cerca.download")

INTERVALLO_CONTROLLO = 15
PREFISSO_ETICHETTA = "mediabox-"
CATEGORIE_SORVEGLIATE = ("cerca", "manuale")


# --- funzioni pure (testate) ---------------------------------------------------

def nome_cartella(titolo: str, anno: str) -> str:
    """"Titolo (Anno)" senza caratteri vietati su Windows o Linux."""
    pulito = unicodedata.normalize("NFC", titolo or "Film")
    pulito = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", pulito)
    pulito = " ".join(pulito.split()).strip(" .") or "Film"
    return f"{pulito} ({anno})" if anno else pulito


def nome_da_magnet(magnet: str) -> str:
    parametri = parse_qs(urlparse(magnet).query)
    nomi = parametri.get("dn") or []
    return unquote(nomi[0]) if nomi else ""


def e_magnet(testo: str) -> bool:
    return testo.strip().lower().startswith("magnet:?")


def etichetta(id_download: int) -> str:
    return f"{PREFISSO_ETICHETTA}{id_download}"


def id_da_etichette(etichette: str) -> int | None:
    for e in (etichette or "").split(","):
        e = e.strip()
        if e.startswith(PREFISSO_ETICHETTA) and e[len(PREFISSO_ETICHETTA):].isdigit():
            return int(e[len(PREFISSO_ETICHETTA):])
    return None


def titoli_simili(a: str, b: str) -> bool:
    def norm(t: str) -> str:
        t = unicodedata.normalize("NFKD", t or "")
        t = "".join(c for c in t if not unicodedata.combining(c)).lower()
        return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())

    return bool(norm(a)) and norm(a) == norm(b)


# --- gestore -------------------------------------------------------------------

class GestoreDownload:
    def __init__(
        self,
        config: Config,
        archivio: Archivio,
        client: httpx.AsyncClient,
        qbit: QBittorrent,
        radarr: Radarr,
        tmdb: TMDB,
        jellyfin: Jellyfin,
    ) -> None:
        self.config = config
        self.archivio = archivio
        self.client = client
        self.qbit = qbit
        self.radarr = radarr
        self.tmdb = tmdb
        self.jellyfin = jellyfin
        # funzione per le notifiche sul telefono (la imposta main.py)
        self.notifica = None
        self._compiti: dict[int, asyncio.Task] = {}
        self._importazioni: set[int] = set()

    # --- avvio dei download ------------------------------------------------------
    async def da_archive_http(self, film: dict, versione: dict) -> int:
        cartella = self.config.cartella_download / "cerca" / nome_cartella(film["titolo"], film["anno"])
        id_download = self.archivio.nuovo_download(
            tmdb_id=film["id"],
            titolo=film["titolo"],
            anno=film["anno"],
            poster=film.get("poster", ""),
            fonte="archive",
            tipo="http",
            url=versione["url"],
            nome_file=versione["nome"],
            percorso=str(cartella / Path(versione["nome"]).name),
            categoria="cerca",
            totale=versione.get("dimensione") or 0,
        )
        self._avvia_http(id_download)
        return id_download

    async def da_archive_torrent(self, film: dict, versione: dict) -> int:
        try:
            r = await self.client.get(versione["url_torrent"], timeout=60)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise ErroreServizio(
                "Non riesco a scaricare il torrent da Internet Archive.", "Prova con il download diretto."
            ) from exc
        id_download = self.archivio.nuovo_download(
            tmdb_id=film["id"], titolo=film["titolo"], anno=film["anno"], poster=film.get("poster", ""),
            fonte="archive", tipo="torrent", url=versione["url_torrent"], nome_file=versione["nome"],
            categoria="cerca",
        )
        await self.qbit.aggiungi(categoria="cerca", etichetta=etichetta(id_download), file_torrent=r.content)
        # Il torrent di Archive contiene tutto l'item: scarichiamo solo il file scelto
        asyncio.create_task(self._solo_file_scelto(id_download, versione["nome"]))
        return id_download

    async def da_torrent(
        self,
        *,
        fonte: str,
        categoria: str,
        film: dict | None,
        titolo: str,
        magnet: str | None = None,
        file_torrent: bytes | None = None,
    ) -> int:
        id_download = self.archivio.nuovo_download(
            tmdb_id=film["id"] if film else None,
            titolo=film["titolo"] if film else (titolo or "Torrent"),
            anno=film["anno"] if film else "",
            poster=film.get("poster", "") if film else "",
            fonte=fonte,
            tipo="torrent",
            url=magnet or "",
            categoria=categoria,
        )
        try:
            await self.qbit.aggiungi(
                categoria=categoria, etichetta=etichetta(id_download), magnet_o_url=magnet, file_torrent=file_torrent
            )
        except ErroreServizio:
            self.archivio.elimina_download(id_download)
            raise
        return id_download

    async def associa(self, id_download: int, film: dict) -> None:
        """Associa un download a un film TMDB e, se è finito, lo passa a Radarr."""
        self.archivio.aggiorna_download(
            id_download, tmdb_id=film["id"], titolo=film["titolo"], anno=film["anno"], poster=film.get("poster", "")
        )
        voce = self.archivio.download(id_download)
        if voce and voce["stato"] in ("da_associare", "errore") and voce.get("percorso"):
            self.archivio.aggiorna_download(id_download, stato="completato", messaggio="")
            asyncio.create_task(self._passa_a_radarr(id_download))

    async def riprova(self, id_download: int) -> None:
        voce = self.archivio.download(id_download)
        if not voce:
            return
        if voce["tipo"] == "http" and voce["stato"] == "errore" and voce.get("scaricati", 0) < (voce["totale"] or 1):
            self.archivio.aggiorna_download(id_download, stato="in_corso", messaggio="")
            self._avvia_http(id_download)
        elif voce.get("percorso") and voce["stato"] in ("errore", "da_associare"):
            self.archivio.aggiorna_download(id_download, stato="completato", messaggio="")
            asyncio.create_task(self._passa_a_radarr(id_download))
        else:
            self.archivio.aggiorna_download(id_download, stato="in_corso", messaggio="")

    async def annulla(self, id_download: int) -> None:
        voce = self.archivio.download(id_download)
        if not voce:
            return
        compito = self._compiti.pop(id_download, None)
        if compito:
            compito.cancel()
        if voce["tipo"] == "torrent" and voce.get("hash") and voce["stato"] != "importato":
            with contextlib.suppress(ErroreServizio):
                await self.qbit.elimina(voce["hash"], con_file=True)
        elif voce["tipo"] == "http" and voce["stato"] != "importato" and voce.get("percorso"):
            for p in (Path(voce["percorso"]), Path(voce["percorso"] + ".part")):
                with contextlib.suppress(OSError):
                    p.unlink()
        self.archivio.elimina_download(id_download)

    # --- download HTTP -----------------------------------------------------------
    def _avvia_http(self, id_download: int) -> None:
        self._compiti[id_download] = asyncio.create_task(self._scarica_http(id_download))

    async def _scarica_http(self, id_download: int) -> None:
        voce = self.archivio.download(id_download)
        if not voce:
            return
        destinazione = Path(voce["percorso"])
        parziale = destinazione.with_name(destinazione.name + ".part")
        destinazione.parent.mkdir(parents=True, exist_ok=True)
        tentativi = 0
        while True:
            gia_scaricati = parziale.stat().st_size if parziale.exists() else 0
            intestazioni = {"Range": f"bytes={gia_scaricati}-"} if gia_scaricati else {}
            try:
                async with self.client.stream(
                    "GET", voce["url"], headers=intestazioni, timeout=httpx.Timeout(60, connect=20)
                ) as r:
                    if r.status_code == 416:  # già completo
                        break
                    r.raise_for_status()
                    if gia_scaricati and r.status_code != 206:
                        gia_scaricati = 0  # il server non riprende: si ricomincia
                    totale = int(r.headers.get("content-length", 0)) + gia_scaricati or voce["totale"]
                    scaricati = gia_scaricati
                    ultimo_salvataggio = time.monotonic()
                    campione_byte, campione_tempo = scaricati, time.monotonic()
                    with open(parziale, "ab" if gia_scaricati else "wb") as f:
                        async for blocco in r.aiter_bytes(1024 * 1024):
                            # sul disco dei film (su Windows lento): scrittura in un thread a parte
                            await asyncio.to_thread(f.write, blocco)
                            scaricati += len(blocco)
                            adesso = time.monotonic()
                            if adesso - ultimo_salvataggio > 2:
                                velocita = int((scaricati - campione_byte) / max(adesso - campione_tempo, 0.1))
                                campione_byte, campione_tempo = scaricati, adesso
                                self.archivio.aggiorna_download(
                                    id_download, scaricati=scaricati, totale=totale, velocita=velocita
                                )
                                ultimo_salvataggio = adesso
                    self.archivio.aggiorna_download(id_download, scaricati=scaricati, totale=totale, velocita=0)
                break
            except asyncio.CancelledError:
                raise
            except (httpx.HTTPError, OSError) as exc:
                tentativi += 1
                log.warning("Download %s interrotto (%s), tentativo %s", id_download, exc, tentativi)
                if tentativi >= 5:
                    self.archivio.aggiorna_download(
                        id_download,
                        stato="errore",
                        velocita=0,
                        messaggio="Il download si è interrotto più volte. Premi \"Riprova\": riparte da dove era.",
                    )
                    return
                await asyncio.sleep(10 * tentativi)
        parziale.replace(destinazione)
        self._compiti.pop(id_download, None)
        self.archivio.aggiorna_download(id_download, stato="completato", velocita=0)
        await self._passa_a_radarr(id_download)

    # --- torrent -------------------------------------------------------------------
    async def _solo_file_scelto(self, id_download: int, nome_file: str) -> None:
        for _ in range(30):
            torrent = await self.qbit.per_etichetta(etichetta(id_download))
            if torrent:
                file = await self.qbit.file(torrent["hash"])
                if file:
                    da_saltare = [f["index"] for f in file if not f["name"].endswith("/" + nome_file)
                                  and f["name"] != nome_file]
                    await self.qbit.priorita_file(torrent["hash"], da_saltare, 0)
                    return
            await asyncio.sleep(2)

    async def _adotta_torrent_esterni(self) -> None:
        """Torrent aggiunti direttamente da qBittorrent nelle categorie di Cerca."""
        for categoria in CATEGORIE_SORVEGLIATE:
            for t in await self.qbit.per_categoria(categoria):
                if id_da_etichette(t.get("tags", "")) is not None:
                    continue
                if self.archivio.download_per_hash(t["hash"]):
                    continue
                id_download = self.archivio.nuovo_download(
                    tmdb_id=None, titolo=t.get("name", "Torrent"), anno="", poster="", fonte="manuale",
                    tipo="torrent", categoria=categoria, hash=t["hash"].lower(),
                )
                await self.qbit.aggiungi_etichetta(t["hash"], etichetta(id_download))

    async def _controlla_torrent(self) -> None:
        for voce in self.archivio.download_per_stato("in_corso"):
            if voce["tipo"] != "torrent":
                continue
            t = await self.qbit.per_etichetta(etichetta(voce["id"]))
            if t is None:
                if time.time() - voce["creato"] > 120:
                    self.archivio.aggiorna_download(
                        voce["id"], stato="errore",
                        messaggio="Il torrent non è più in qBittorrent (forse è stato rimosso).",
                    )
                continue
            campi = {
                "hash": t["hash"].lower(),
                "scaricati": int(t.get("completed") or 0),
                "totale": int(t.get("size") or 0),
                "velocita": int(t.get("dlspeed") or 0),
            }
            if not voce["tmdb_id"] and t.get("name") and voce["titolo"] in ("Torrent", ""):
                campi["titolo"] = t["name"]
            if t.get("state") in STATI_ERRORE:
                campi.update(stato="errore", messaggio="qBittorrent segnala un errore: aprilo per i dettagli.")
            elif t.get("progress", 0) >= 1 or t.get("state") in STATI_FINITI:
                campi.update(stato="completato", percorso=t.get("content_path") or "", velocita=0)
            self.archivio.aggiorna_download(voce["id"], **campi)
            if campi.get("stato") == "completato":
                asyncio.create_task(self._passa_a_radarr(voce["id"]))

    # --- passaggio a Radarr ----------------------------------------------------------
    async def _indovina_film(self, voce: dict) -> dict | None:
        """Per i torrent senza film associato: prova a capire quale film è."""
        nome = voce.get("titolo") or Path(voce.get("percorso") or "").name
        if not nome:
            return None
        try:
            analisi = await self.radarr.analizza_titolo(nome)
            if not analisi["titolo"]:
                return None
            candidati = await self.tmdb.cerca(analisi["titolo"])
        except ErroreServizio:
            return None
        for c in candidati[:5]:
            stesso_titolo = titoli_simili(c["titolo"], analisi["titolo"]) or titoli_simili(
                c["titolo_originale"], analisi["titolo"]
            )
            if stesso_titolo and (not analisi["anno"] or c["anno"] == analisi["anno"]):
                return c
        return None

    async def _passa_a_radarr(self, id_download: int) -> None:
        if id_download in self._importazioni:
            return
        self._importazioni.add(id_download)
        try:
            voce = self.archivio.download(id_download)
            if not voce or voce["stato"] != "completato":
                return
            if not voce["tmdb_id"]:
                film = await self._indovina_film(voce)
                if not film:
                    self.archivio.aggiorna_download(
                        id_download, stato="da_associare",
                        messaggio="Download finito. Dimmi di che film si tratta e lo metto in Jellyfin.",
                    )
                    return
                self.archivio.aggiorna_download(
                    id_download, tmdb_id=film["id"], titolo=film["titolo"], anno=film["anno"], poster=film["poster"]
                )
                voce = self.archivio.download(id_download)
            self.archivio.aggiorna_download(id_download, stato="importazione", messaggio="")
            film_radarr = await self.radarr.aggiungi_film(voce["tmdb_id"])
            cartella_film = await self.radarr.importa(voce["percorso"], film_radarr["id"])
            if cartella_film:
                await self.jellyfin.avvisa_nuovo(cartella_film)
            if voce["tipo"] == "torrent" and voce.get("hash"):
                # Il torrent è già fermo (rapporto 0): togliamo gli avanzi
                with contextlib.suppress(ErroreServizio):
                    await self.qbit.elimina(voce["hash"], con_file=True)
            elif voce["tipo"] == "http":
                with contextlib.suppress(OSError):
                    Path(voce["percorso"]).parent.rmdir()
            self.archivio.aggiorna_download(id_download, stato="importato", messaggio="")
            if self.notifica:
                anno = f" ({voce['anno']})" if voce.get("anno") else ""
                await self.notifica(
                    "Pronto da guardare",
                    f"{voce['titolo']}{anno} è in Jellyfin.",
                    f"{self.config.indirizzo_pubblico}/jellyfin/",
                )
        except ErroreServizio as exc:
            self.archivio.aggiorna_download(
                id_download, stato="errore", messaggio=f"{exc.cosa} {exc.cosa_fare}".strip()
            )
        except Exception:  # noqa: BLE001 - qualsiasi problema va mostrato, non deve fermare il servizio
            log.exception("Errore inatteso nell'importazione %s", id_download)
            self.archivio.aggiorna_download(
                id_download, stato="errore",
                messaggio="Errore inatteso durante il passaggio a Radarr. Premi \"Riprova\".",
            )
        finally:
            self._importazioni.discard(id_download)

    # --- ciclo di controllo ------------------------------------------------------------
    async def riprendi_all_avvio(self) -> None:
        """Dopo un riavvio riprende i download HTTP e le importazioni a metà."""
        for voce in self.archivio.download_per_stato("in_corso", "importazione", "completato"):
            if voce["stato"] == "in_corso" and voce["tipo"] == "http":
                self._avvia_http(voce["id"])
            elif voce["stato"] in ("importazione", "completato"):
                self.archivio.aggiorna_download(voce["id"], stato="completato")
                asyncio.create_task(self._passa_a_radarr(voce["id"]))

    async def ciclo(self) -> None:
        await self.riprendi_all_avvio()
        while True:
            try:
                await self._adotta_torrent_esterni()
                await self._controlla_torrent()
            except ErroreServizio as exc:
                log.info("qBittorrent non disponibile: %s", exc.cosa)
            except Exception:  # noqa: BLE001
                log.exception("Errore nel controllo dei download")
            await asyncio.sleep(INTERVALLO_CONTROLLO)
