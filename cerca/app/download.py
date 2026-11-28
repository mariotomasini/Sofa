"""Gestione dei download: HTTP diretto da Internet Archive e dai siti degli anime, torrent via
qBittorrent, e passaggio finale a Radarr (che mette il film in Jellyfin) o a Sonarr."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import shutil
import time
import unicodedata
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from .config import Config
from .db import Archivio
from .jellyfin import Jellyfin
from .musica_download import file_da_saltare
from .qbittorrent import STATI_ERRORE, STATI_FINITI, STATI_IN_MOVIMENTO, QBittorrent
from .radarr import Radarr
from .rete import ErroreServizio, scarica_torrent
from .tmdb import TMDB

FONTE_IMPORTAZIONE = "importazione"
log = logging.getLogger("cerca.download")

INTERVALLO_CONTROLLO = 15
PREFISSO_ETICHETTA = "mediabox-"
CATEGORIE_SORVEGLIATE = ("cerca", "manuale")
CATEGORIA_MUSICA = "musica"
# download HTTP contemporanei per fonte: i siti degli anime non amano troppe richieste insieme
CONTEMPORANEI = {"animeunity": 2, "animeworld": 2, "streamingcommunity": 2}
BLOCCO_SCRITTURA = 1024 * 1024
MAX_TENTATIVI = 5  # interruzioni di fila senza nessun byte in più, poi "errore"
CAMBIO_SITO = 2  # dopo quante interruzioni di fila si prova il sito di riserva
FFMPEG_FERMO = 180  # secondi senza avanzamento: ffmpeg è bloccato, si riprova
FFMPEG_TIMEOUT_RETE = 30_000_000  # microsecondi: una lettura di rete ferma fa uscire ffmpeg con un errore


class DownloadIncompleto(Exception):
    """Il server ha chiuso prima della fine, o ha mandato un pezzo che non combacia: si riprova."""


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


def ids_da_etichette(etichette: str) -> list[int]:
    """Tutti i download di Cerca di un torrent (di solito uno; due se lo stesso torrent è stato chiesto due volte)."""
    ids = []
    for e in (etichette or "").split(","):
        e = e.strip()
        if e.startswith(PREFISSO_ETICHETTA) and e[len(PREFISSO_ETICHETTA):].isdigit():
            ids.append(int(e[len(PREFISSO_ETICHETTA):]))
    return ids


def range_del_server(intestazione: str) -> tuple[int | None, int | None]:
    """Da "bytes 100-199/1000" → (100, 1000); da "bytes */1000" (risposta 416) → (None, 1000)."""
    m = re.fullmatch(r"\s*bytes\s+(?:(\d+)-\d+|\*)/(\d+|\*)\s*", intestazione or "")
    if not m:
        return None, None
    inizio = int(m.group(1)) if m.group(1) is not None else None
    totale = int(m.group(2)) if m.group(2) != "*" else None
    return inizio, totale


def fonti_ruotate(url: str, giro: int) -> str:
    """"diretto:[[sito A, id], [sito B, id]]" con il sito di riserva per primo dopo `giro` cambi."""
    if not url.startswith("diretto:") or not giro:
        return url
    try:
        fonti = json.loads(url[len("diretto:"):])
    except ValueError:
        return url
    if not isinstance(fonti, list) or len(fonti) < 2:
        return url
    n = giro % len(fonti)
    return "diretto:" + json.dumps(fonti[n:] + fonti[:n])


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
        self.serie = None  # Serie (Sonarr): la imposta main.py
        self._compiti: dict[int, asyncio.Task] = {}
        self._importazioni: set[int] = set()
        # asyncio tiene solo un riferimento debole ai compiti: senza questo insieme un compito
        # in sottofondo (es. il passaggio a Radarr) poteva sparire a metà senza lasciare traccia
        self._in_sottofondo: set[asyncio.Task] = set()
        # fonte → funzione che dà il link vero (e le intestazioni) al momento di scaricare:
        # i link dei siti degli anime scadono, quindi si chiedono a ogni partenza (li imposta main.py)
        self.risolutori: dict = {}
        self._turni = {fonte: asyncio.Semaphore(n) for fonte, n in CONTEMPORANEI.items()}

    def _sottofondo(self, coro) -> asyncio.Task:
        """Avvia un compito in sottofondo tenendolo in vita fino alla fine, e ne registra gli errori."""
        compito = asyncio.create_task(coro)
        self._in_sottofondo.add(compito)
        compito.add_done_callback(self._fine_sottofondo)
        return compito

    def _fine_sottofondo(self, compito: asyncio.Task) -> None:
        self._in_sottofondo.discard(compito)
        if not compito.cancelled() and (exc := compito.exception()) is not None:
            log.error("Errore in un compito dei download", exc_info=exc)

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

    async def da_diretto(self, *, serie_id: int, titolo_serie: str, poster: str, episodio: dict,
                         fonti: list[list], nome_file: str, doppiato: bool) -> int:
        """Una puntata da un sito diretto (AnimeUnity, AnimeWorld): HTTP, poi a Sonarr come le altre.
        `fonti` = [[sito, id puntata], …] nell'ordine in cui provarli: il link si chiede a ogni partenza."""
        cartella = self.config.cartella_download / "cerca" / "anime" / nome_cartella(titolo_serie, "")
        lingua = "ITA" if doppiato else "SUB ITA"
        id_download = self.archivio.nuovo_download(
            tmdb_id=None, titolo=f"{titolo_serie} · Stagione {episodio['seasonNumber']} · "
                                 f"Episodio {episodio['episodeNumber']} ({lingua})",
            anno="", poster=poster, fonte=fonti[0][0], tipo="http", url="diretto:" + json.dumps(fonti),
            nome_file=nome_file, percorso=str(cartella / nome_file), categoria="cerca", serie=serie_id,
            puntate=json.dumps([episodio["id"]]), totale=0,
        )
        self._avvia_http(id_download)
        return id_download

    async def da_diretto_film(self, film: dict, fonti: list[list], nome_file: str) -> int:
        """Un film da un sito diretto (StreamingCommunity): poi a Radarr come gli altri."""
        cartella = self.config.cartella_download / "cerca" / nome_cartella(film["titolo"], film.get("anno", ""))
        id_download = self.archivio.nuovo_download(
            tmdb_id=film["id"], titolo=film["titolo"], anno=film.get("anno", ""), poster=film.get("poster", ""),
            fonte=fonti[0][0], tipo="http", url="diretto:" + json.dumps(fonti), nome_file=nome_file,
            percorso=str(cartella / nome_file), categoria="cerca", totale=0,
        )
        self._avvia_http(id_download)
        return id_download

    async def da_archive_torrent(self, film: dict, versione: dict) -> int:
        try:
            magnet, contenuto = await scarica_torrent(self.client, versione["url_torrent"])
        except (httpx.HTTPError, ValueError) as exc:
            raise ErroreServizio(
                "Non riesco a scaricare il torrent da Internet Archive.", "Prova con il download diretto."
            ) from exc
        id_download = self.archivio.nuovo_download(
            tmdb_id=film["id"], titolo=film["titolo"], anno=film["anno"], poster=film.get("poster", ""),
            fonte="archive", tipo="torrent", url=versione["url_torrent"], nome_file=versione["nome"],
            categoria="cerca",
        )
        try:
            await self.qbit.aggiungi(categoria="cerca", etichetta=etichetta(id_download),
                                     magnet_o_url=magnet, file_torrent=contenuto or None)
        except ErroreServizio:
            # come in da_torrent: niente voce "in corso" per un torrent che qBittorrent non ha
            self.archivio.elimina_download(id_download)
            raise
        # Il torrent di Archive contiene tutto l'item: scarichiamo solo il file scelto
        self._sottofondo(self._solo_file_scelto(id_download, versione["nome"]))
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
        serie: int | None = None,
        poster: str = "",
        cartella: str | None = None,
        solo_brano: str = "",
        id_download: int | None = None,
    ) -> int:
        """Aggiunge il torrent a qBittorrent. Con id_download usa una voce già in elenco
        (la musica, che compare subito mentre la cerco sugli indexer)."""
        gia_in_elenco = id_download is not None
        if id_download is None:
            id_download = self.archivio.nuovo_download(
                tmdb_id=film["id"] if film else None,
                titolo=film["titolo"] if film else (titolo or "Torrent"),
                anno=film["anno"] if film else "",
                poster=film.get("poster", "") if film else poster,
                serie=serie,
                fonte=fonte,
                tipo="torrent",
                url=magnet or "",
                categoria=categoria,
            )
        try:
            await self.qbit.aggiungi(
                categoria=categoria, etichetta=etichetta(id_download), magnet_o_url=magnet, file_torrent=file_torrent,
                cartella=cartella,
            )
        except ErroreServizio:
            if not gia_in_elenco:
                self.archivio.elimina_download(id_download)
            raise
        if gia_in_elenco:
            # "creato" riparte da adesso: il controllo dà a qBittorrent i soliti 2 minuti per mostrarlo
            self.archivio.aggiorna_download(id_download, stato="in_corso", url=magnet or "", messaggio="",
                                            creato=time.time())
        if solo_brano:
            self._sottofondo(self._solo_brano(id_download, solo_brano))
        return id_download

    def cerca_in_sottofondo(self, id_download: int, coro) -> None:
        """Una ricerca lunga che finisce con un torrent (la musica sugli indexer). La voce è già
        nell'elenco: se non trova niente mostra il perché, e "Annulla" la ferma."""
        async def esegui() -> None:
            try:
                await coro
            except asyncio.CancelledError:
                raise
            except ErroreServizio as exc:
                self.archivio.aggiorna_download(id_download, stato="errore", messaggio=f"{exc.cosa} {exc.cosa_fare}".strip())
            except Exception:  # noqa: BLE001 - la voce non deve restare "cerco" per sempre
                log.exception("Errore inatteso nella ricerca %s", id_download)
                self.archivio.aggiorna_download(
                    id_download, stato="errore",
                    messaggio="La ricerca si è fermata per un errore inatteso. Premi \"Riprova\".")
            finally:
                if self._compiti.get(id_download) is asyncio.current_task():
                    self._compiti.pop(id_download, None)

        self._compiti[id_download] = asyncio.create_task(esegui())

    async def _solo_brano(self, id_download: int, titolo: str) -> None:
        """Da un album intero scarica solo il brano scelto (appena qBittorrent conosce i file)."""
        for _ in range(90):
            torrent = await self.qbit.per_etichetta(etichetta(id_download))
            if torrent:
                file = await self.qbit.file(torrent["hash"])
                if file:
                    await self.qbit.priorita_file(torrent["hash"], file_da_saltare(file, titolo), 0)
                    return
            await asyncio.sleep(2)

    async def associa(self, id_download: int, film: dict) -> None:
        """Associa un download a un film TMDB e, se è finito, lo passa a Radarr."""
        self.archivio.aggiorna_download(
            id_download, tmdb_id=film["id"], titolo=film["titolo"], anno=film["anno"], poster=film.get("poster", "")
        )
        voce = self.archivio.download(id_download)
        if voce and voce["stato"] in ("da_associare", "errore") and voce.get("percorso"):
            self.archivio.aggiorna_download(id_download, stato="completato", messaggio="")
            self._sottofondo(self._passa_a_radarr(id_download))

    async def riprova(self, id_download: int) -> None:
        voce = self.archivio.download(id_download)
        if not voce:
            return
        # un download HTTP si rifà se il file finito non c'è: prima si guardavano i byte contati, che
        # per lo streaming (HLS) sono una stima e potevano mandare a Radarr un file mai finito
        if voce["tipo"] == "http" and voce["stato"] == "errore" and voce.get("percorso") \
                and not await asyncio.to_thread(Path(voce["percorso"]).exists):
            self.archivio.aggiorna_download(id_download, stato="in_corso", messaggio="")
            self._avvia_http(id_download)
        elif voce.get("percorso") and voce["stato"] in ("errore", "da_associare"):
            self.archivio.aggiorna_download(id_download, stato="completato", messaggio="")
            self._sottofondo(self._passa_a_radarr(id_download))
        else:
            self.archivio.aggiorna_download(id_download, stato="in_corso", messaggio="")

    async def annulla(self, id_download: int) -> None:
        voce = self.archivio.download(id_download)
        if not voce:
            return
        compito = self._compiti.pop(id_download, None)
        if compito:
            compito.cancel()
            # si aspetta che il download si fermi davvero (file chiuso), poi si cancellano i file:
            # su Windows un file ancora aperto non si cancella e restava sul disco per sempre
            if compito is not asyncio.current_task():
                await asyncio.wait({compito}, timeout=5)
        if voce["tipo"] == "torrent" and voce["stato"] != "importato":
            with contextlib.suppress(ErroreServizio):
                hash_torrent = voce.get("hash")
                if not hash_torrent:  # appena aggiunto: qBittorrent lo conosce già, l'elenco non ancora
                    torrent = await self.qbit.per_etichetta(etichetta(id_download))
                    hash_torrent = torrent["hash"] if torrent else ""
                if hash_torrent:
                    await self.qbit.elimina(hash_torrent, con_file=True)
        elif voce["tipo"] == "http" and voce["stato"] != "importato" and voce.get("percorso"):
            for p in (Path(voce["percorso"]), Path(voce["percorso"] + ".part")):
                with contextlib.suppress(OSError):
                    p.unlink()
        self.archivio.elimina_download(id_download)

    # --- download HTTP -----------------------------------------------------------
    def _avvia_http(self, id_download: int) -> None:
        attuale = self._compiti.get(id_download)
        if attuale is not None and not attuale.done():
            return  # già partito: due compiti sullo stesso file .part lo rovinerebbero
        self._compiti[id_download] = asyncio.create_task(self._scarica_http_sicuro(id_download))

    async def _scarica_http_sicuro(self, id_download: int) -> None:
        """Un errore inatteso non deve lasciare il download "in corso" per sempre."""
        try:
            await self._scarica_http(id_download)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - qualsiasi problema va mostrato con il tasto "Riprova"
            log.exception("Errore inatteso nel download %s", id_download)
            self.archivio.aggiorna_download(
                id_download, stato="errore", velocita=0,
                messaggio="Il download si è fermato per un errore inatteso. Premi \"Riprova\": riparte da dove era.",
            )
        finally:
            if self._compiti.get(id_download) is asyncio.current_task():
                self._compiti.pop(id_download, None)

    async def _scarica_http(self, id_download: int) -> None:
        voce = self.archivio.download(id_download)
        if not voce:
            return
        turno = self._turni.get(voce.get("fonte") or "")
        if turno is None:
            await self._scarica_http_ora(id_download, voce)
            return
        if turno.locked():
            self.archivio.aggiorna_download(id_download, messaggio="In coda: parte appena finisce un'altra puntata.")
        async with turno:
            self.archivio.aggiorna_download(id_download, messaggio="")
            await self._scarica_http_ora(id_download, voce)

    async def _link(self, voce: dict) -> tuple[str, dict]:
        risolvi = self.risolutori.get(voce.get("fonte") or "")
        return await risolvi(voce) if risolvi else (voce["url"], {})

    async def _scarica_http_ora(self, id_download: int, voce: dict) -> None:
        destinazione = Path(voce["percorso"])
        parziale = destinazione.with_name(destinazione.name + ".part")
        destinazione.parent.mkdir(parents=True, exist_ok=True)
        tentativi = giro = 0
        while True:
            gia_scaricati = parziale.stat().st_size if parziale.exists() else 0
            try:
                url, intestazioni = await self._link({**voce, "url": fonti_ruotate(str(voce.get("url") or ""), giro)})
                if url.startswith("hls:"):
                    await self._scarica_hls(id_download, parziale, json.loads(url[4:]), intestazioni)
                else:
                    await self._scarica_flusso(id_download, url, intestazioni, parziale, gia_scaricati,
                                               voce.get("totale") or 0)
                break
            except asyncio.CancelledError:
                raise
            except (httpx.HTTPError, OSError, ErroreServizio, DownloadIncompleto) as exc:
                # se questo tentativo ha aggiunto byte il conto riparte: un file grande su una linea
                # che cade spesso arriva comunque alla fine, invece di fermarsi alla quinta caduta
                avanzato = (parziale.stat().st_size if parziale.exists() else 0) > gia_scaricati
                tentativi = 1 if avanzato else tentativi + 1
                log.warning("Download %s interrotto (%s), tentativo %s", id_download, exc, tentativi)
                if tentativi >= MAX_TENTATIVI:
                    if await self._riserva_radarr(voce):
                        return
                    messaggio = (f"{exc.cosa} {exc.cosa_fare}".strip() if isinstance(exc, ErroreServizio) else
                                 "Il download si è interrotto più volte. Premi \"Riprova\": riparte da dove era.")
                    self.archivio.aggiorna_download(id_download, stato="errore", velocita=0, messaggio=messaggio)
                    return
                if tentativi % CAMBIO_SITO == 0 and fonti_ruotate(str(voce.get("url") or ""), 1) != voce.get("url"):
                    # il sito non scarica (non solo il link): si passa a quello di riserva, da capo
                    # (un altro sito ha un altro file: non si può continuare il pezzo già scaricato)
                    giro += 1
                    with contextlib.suppress(OSError):
                        parziale.unlink()
                    log.info("Download %s: provo il sito di riserva", id_download)
                await asyncio.sleep(10 * tentativi)
        parziale.replace(destinazione)
        self._compiti.pop(id_download, None)  # da qui "Annulla" non interrompe più l'importazione
        self.archivio.aggiorna_download(id_download, stato="completato", velocita=0)
        await self._passa_a_radarr(id_download)

    async def _scarica_flusso(self, id_download: int, url: str, intestazioni: dict, parziale: Path,
                              gia_scaricati: int, totale_noto: int) -> None:
        """Scarica (o riprende) il file in `parziale`. Torna solo se il file è completo, altrimenti
        un'eccezione: un pezzo mancante o fuori posto non deve mai diventare un film "finito"."""
        intestazioni = {**intestazioni, **({"Range": f"bytes={gia_scaricati}-"} if gia_scaricati else {})}
        async with self.client.stream("GET", url, headers=intestazioni, timeout=httpx.Timeout(60, connect=20)) as r:
            if r.status_code == 416:
                # "niente oltre questo punto": completo solo se le dimensioni tornano
                _, totale_server = range_del_server(r.headers.get("content-range", ""))
                if gia_scaricati and gia_scaricati == (totale_server or totale_noto):
                    return
                with contextlib.suppress(OSError):
                    parziale.unlink()  # più grande del file vero (cambiato sul sito): da capo
                raise DownloadIncompleto("il pezzo già scaricato non combacia con il file del sito")
            r.raise_for_status()
            if gia_scaricati:
                if r.status_code != 206:
                    gia_scaricati = 0  # il server non riprende e manda tutto: si ricomincia
                else:
                    inizio, _ = range_del_server(r.headers.get("content-range", ""))
                    if inizio is not None and inizio != gia_scaricati:
                        with contextlib.suppress(OSError):
                            parziale.unlink()
                        raise DownloadIncompleto(f"il server riprende da {inizio} invece che da {gia_scaricati}")
            try:
                lunghezza = int(r.headers.get("content-length") or 0)
            except ValueError:
                lunghezza = 0
            totale = lunghezza + gia_scaricati if lunghezza else totale_noto
            scaricati = gia_scaricati
            ultimo_salvataggio = campione_tempo = time.monotonic()
            campione_byte = scaricati
            # i byte arrivano come arrivano e si scrivono a blocchi da 1 MB (sul disco dei film, su
            # Windows lento, in un thread a parte). Con aiter_bytes(1 MB) httpx teneva da parte fino
            # a 1 MB: se la linea cadeva prima, quei byte si perdevano e su un sito che cade spesso
            # il download non andava mai avanti. Ora quello che è arrivato si scrive sempre.
            in_memoria = bytearray()
            with open(parziale, "ab" if gia_scaricati else "wb") as f:
                try:
                    async for blocco in r.aiter_bytes():
                        in_memoria += blocco
                        scaricati += len(blocco)
                        if len(in_memoria) >= BLOCCO_SCRITTURA:
                            await asyncio.to_thread(f.write, bytes(in_memoria))
                            in_memoria.clear()
                        adesso = time.monotonic()
                        if adesso - ultimo_salvataggio > 2:
                            velocita = int((scaricati - campione_byte) / max(adesso - campione_tempo, 0.1))
                            campione_byte, campione_tempo = scaricati, adesso
                            self.archivio.aggiorna_download(id_download, scaricati=scaricati, totale=totale,
                                                            velocita=velocita)
                            ultimo_salvataggio = adesso
                finally:
                    if in_memoria:
                        await asyncio.to_thread(f.write, bytes(in_memoria))
            self.archivio.aggiorna_download(id_download, scaricati=scaricati, totale=totale, velocita=0)
            if lunghezza and scaricati < totale:
                raise DownloadIncompleto(f"arrivati {scaricati} byte su {totale}")

    async def _riserva_radarr(self, voce: dict) -> bool:
        """Un film da un sito diretto che non si scarica: lo passo a Radarr (torrent), senza fermarmi."""
        if voce.get("serie") or not voce.get("tmdb_id") or not str(voce.get("url") or "").startswith("diretto:"):
            return False
        try:
            await self.radarr.scarica_da_solo(voce["tmdb_id"])
        except ErroreServizio:
            return False
        for p in (Path(voce["percorso"]), Path(voce["percorso"] + ".part")):
            with contextlib.suppress(OSError):
                p.unlink()
        self.archivio.elimina_download(voce["id"])
        log.info("Download diretto di %s non riuscito: ci pensa Radarr", voce.get("titolo"))
        return True

    async def _scarica_hls(self, id_download: int, parziale: Path, flussi: dict, intestazioni: dict) -> None:
        """Video (e audio italiano, se è a parte) con ffmpeg, in un unico file MKV. Lo streaming a pezzi
        non si riprende da metà: se si interrompe, ricomincia. L'avanzamento è stimato dalla durata."""
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise ErroreServizio("Manca ffmpeg, che serve per questo sito.", "Aggiorna mediabox (\"mediabox update\").")
        testata = "".join(f"{k}: {v}\r\n" for k, v in intestazioni.items())
        # rw_timeout: una connessione ferma fa uscire ffmpeg con un errore (e si riprova) invece di aspettare per sempre
        ingresso = ["-rw_timeout", str(FFMPEG_TIMEOUT_RETE), "-headers", testata, "-i"]
        comando = [ffmpeg, "-nostdin", "-y", "-loglevel", "error", "-progress", "pipe:1", *ingresso, flussi["video"]]
        if flussi.get("audio"):
            comando += [*ingresso, flussi["audio"], "-map", "0:v:0", "-map", "1:a:0"]
        else:
            comando += ["-map", "0:v:0", "-map", "0:a?"]
        comando += ["-c", "copy", "-f", "matroska", str(parziale)]
        banda = int(flussi.get("banda") or 0) or 4_000_000
        totale = int(banda / 8 * float(flussi.get("durata") or 0))
        processo = await asyncio.create_subprocess_exec(*comando, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE)
        # gli errori si leggono intanto: se nessuno svuota stderr, a 64 KB di messaggi (un flusso che
        # dà errori a ogni pezzo) ffmpeg si ferma ad aspettare e il download restava "in corso" per sempre
        coda_errori = asyncio.create_task(_ultimi_byte(processo.stderr, 4096))
        try:
            ultimo = 0.0
            while True:
                try:
                    riga = await asyncio.wait_for(processo.stdout.readline(), FFMPEG_FERMO)
                except TimeoutError:
                    raise ErroreServizio("Lo streaming si è fermato (nessun dato da qualche minuto).",
                                         "Riprovo da solo; se non va, premi \"Riprova\".") from None
                if not riga:
                    break
                chiave, _, valore = riga.decode(errors="ignore").strip().partition("=")
                if chiave == "out_time_us" and valore.isdigit() and time.monotonic() - ultimo > 2:
                    fatti = int(banda / 8 * int(valore) / 1_000_000)
                    self.archivio.aggiorna_download(id_download, scaricati=fatti, totale=max(totale, fatti))
                    ultimo = time.monotonic()
            codice = await processo.wait()
            errore = (await coda_errori).decode(errors="ignore").strip()
            if codice != 0:
                with contextlib.suppress(OSError):
                    parziale.unlink()
                log.warning("ffmpeg: %s", errore[-500:])
                raise ErroreServizio("Il download a pezzi (HLS) si è interrotto.", "Premi \"Riprova\": riparte da capo.")
        except BaseException:
            # annullato, bloccato o finito male: ffmpeg si ferma davvero (niente processi orfani che
            # scrivono ancora il file mentre lo si cancella)
            if processo.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    processo.kill()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(processo.wait(), 5)
            coda_errori.cancel()
            raise
        dimensione = parziale.stat().st_size if parziale.exists() else 0
        self.archivio.aggiorna_download(id_download, scaricati=dimensione, totale=dimensione, velocita=0)

    # --- torrent -------------------------------------------------------------------
    @staticmethod
    def _per_id(torrent: list[dict]) -> dict[int, dict]:
        per_id: dict[int, dict] = {}
        for t in torrent:
            for i in ids_da_etichette(t.get("tags", "")):
                per_id.setdefault(i, t)
        return per_id

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

    async def _adotta_torrent_esterni(self, torrent: list[dict]) -> None:
        """Torrent aggiunti direttamente da qBittorrent nelle categorie di Cerca."""
        for categoria in CATEGORIE_SORVEGLIATE:
            for t in (x for x in torrent if x.get("category") == categoria):
                if id_da_etichette(t.get("tags", "")) is not None:
                    continue
                if self.archivio.download_per_hash(t["hash"]):
                    continue
                id_download = self.archivio.nuovo_download(
                    tmdb_id=None, titolo=t.get("name", "Torrent"), anno="", poster="", fonte="manuale",
                    tipo="torrent", categoria=categoria, hash=t["hash"].lower(),
                )
                await self.qbit.aggiungi_etichetta(t["hash"], etichetta(id_download))

    async def _controlla_torrent(self, torrent: list[dict]) -> None:
        """Con un solo elenco di qBittorrent per giro: prima una richiesta per ogni download attivo
        (con 30 puntate in coda, 30 richieste ogni 15 secondi)."""
        per_id = self._per_id(torrent)
        for voce in self.archivio.download_per_stato("in_corso"):
            if voce["tipo"] != "torrent":
                continue
            t = per_id.get(voce["id"])
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
            # prima dei metadati di un magnet il "nome" è l'hash: non lo si tiene come titolo
            if not voce["tmdb_id"] and t.get("name") and voce["titolo"] in ("Torrent", "") \
                    and t["name"].lower() != str(t.get("hash", "")).lower():
                campi["titolo"] = t["name"]
            if t.get("state") in STATI_ERRORE:
                campi.update(stato="errore", messaggio="qBittorrent segnala un errore: aprilo per i dettagli.")
            elif t.get("state") in STATI_IN_MOVIMENTO:
                pass  # finito, ma i file si stanno spostando: il percorso giusto arriva dopo
            elif t.get("progress", 0) >= 1 or t.get("state") in STATI_FINITI:
                campi.update(stato="completato", percorso=t.get("content_path") or "", velocita=0)
            self.archivio.aggiorna_download(voce["id"], **campi)
            if campi.get("stato") == "completato":
                self._sottofondo(self._passa_a_radarr(voce["id"]))

    async def _musica_pronta(self, voce: dict) -> None:
        """La musica è già nella sua cartella: si toglie il torrent (non i file) e si avvisa Jellyfin."""
        if voce.get("hash"):
            with contextlib.suppress(ErroreServizio):
                await self.qbit.elimina(voce["hash"], con_file=False)
        if voce.get("percorso"):
            await self.jellyfin.avvisa_nuovo(voce["percorso"])
        self.archivio.aggiorna_download(voce["id"], stato="importato", messaggio="")
        # i brani delle playlist importate: un avviso solo, a importazione finita (importa_musica.py)
        if self.notifica and voce.get("fonte") != FONTE_IMPORTAZIONE:
            await self.notifica("Pronto da ascoltare", f"{voce['titolo']} è in Jellyfin.",
                                f"{self.config.indirizzo_pubblico}/jellyfin/")

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

    async def _passa_a_sonarr(self, voce: dict) -> None:
        """Le puntate di una serie vanno a Sonarr (che poi avvisa Jellyfin)."""
        id_download = voce["id"]
        self.archivio.aggiorna_download(id_download, stato="importazione", messaggio="")
        quante, cartella_serie = await self.serie.importa(voce["percorso"], voce["serie"])
        if cartella_serie:
            # come per i film: avviso e scansione, perché Jellyfin ignora gli avvisi per le cartelle nuove
            await self.jellyfin.avvisa_nuovo(cartella_serie)
        if voce["tipo"] == "torrent" and voce.get("hash"):
            with contextlib.suppress(ErroreServizio):
                await self.qbit.elimina(voce["hash"], con_file=True)
        elif voce["tipo"] == "http":
            with contextlib.suppress(OSError):
                Path(voce["percorso"]).parent.rmdir()
        self.archivio.aggiorna_download(id_download, stato="importato", messaggio="")
        if self.notifica:
            puntate = "1 puntata" if quante == 1 else f"{quante} puntate"
            await self.notifica("Pronto da guardare", f"{voce['titolo']}: {puntate} in Jellyfin.",
                                f"{self.config.indirizzo_pubblico}/jellyfin/")

    async def _passa_a_radarr(self, id_download: int) -> None:
        if id_download in self._importazioni:
            return
        self._importazioni.add(id_download)
        try:
            voce = self.archivio.download(id_download)
            if not voce or voce["stato"] != "completato":
                return
            if voce.get("serie") and self.serie is not None:
                await self._passa_a_sonarr(voce)
                return
            if voce.get("categoria") == CATEGORIA_MUSICA:
                await self._musica_pronta(voce)
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
        for voce in self.archivio.download_per_stato("ricerca"):
            self.archivio.aggiorna_download(
                voce["id"], stato="errore", messaggio="La ricerca si è interrotta (Cerca è ripartita). Premi \"Riprova\".")
        for voce in self.archivio.download_per_stato("in_corso", "importazione", "completato"):
            if voce["stato"] == "in_corso" and voce["tipo"] == "http":
                self._avvia_http(voce["id"])
            elif voce["stato"] in ("importazione", "completato"):
                self.archivio.aggiorna_download(voce["id"], stato="completato")
                self._sottofondo(self._passa_a_radarr(voce["id"]))

    async def ciclo(self) -> None:
        await self.riprendi_all_avvio()
        while True:
            try:
                torrent = await self.qbit.tutti()
                await self._adotta_torrent_esterni(torrent)
                await self._controlla_torrent(torrent)
            except ErroreServizio as exc:
                log.info("qBittorrent non disponibile: %s", exc.cosa)
            except Exception:  # noqa: BLE001
                log.exception("Errore nel controllo dei download")
            await asyncio.sleep(INTERVALLO_CONTROLLO)


async def _ultimi_byte(flusso: asyncio.StreamReader, quanti: int) -> bytes:
    """Legge tutto il flusso tenendo solo gli ultimi `quanti` byte (gli errori di ffmpeg)."""
    coda = b""
    while blocco := await flusso.read(65536):
        coda = (coda + blocco)[-quanti:]
    return coda
