"""I download che fanno da soli Radarr e Sonarr, con la lingua in evidenza.

- coda(): cosa stanno scaricando adesso, con la lingua dell'audio;
- in_attesa(): film e puntate già scaricati ma non nella lingua preferita
  (verranno sostituiti appena esce la versione giusta);
- ricerca_notturna(): ogni notte fa ricercare a Radarr e Sonarr le versioni
  migliori e quello che non avevano trovato, per non affidarsi solo alle uscite nuove.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

from .config import Config
from .radarr import Radarr
from .serie import Serie

log = logging.getLogger("cerca.automatici")

NOMI = {"it": "Italiano", "es": "Spagnolo", "fr": "Francese", "de": "Tedesco", "pt": "Portoghese", "en": "Inglese"}
DA_INGLESE = {"Italian": "Italiano", "Spanish": "Spagnolo", "French": "Francese", "German": "Tedesco",
              "Portuguese": "Portoghese", "English": "Inglese"}
PREFISSO = "Lingua · "
ORA_RICERCA = 4  # di notte, quando nessuno guarda
CACHE_ATTESA = 600


def lingua_da(formati: list[dict] | None, lingue: list[dict] | None = None,
              ordine: tuple[str, ...] = ("it", "es", "en")) -> str:
    """La lingua dell'audio: dai formati di mediabox ("Lingua · Spagnolo"), se no da quella letta da Radarr.
    Con più lingue (es. ITA.SPA) conta quella più in alto nelle preferenze."""
    trovate = [f.get("name", "").removeprefix(PREFISSO) for f in formati or []
               if f.get("name", "").startswith(PREFISSO) and f.get("name") != PREFISSO + "MULTI"]
    if not trovate:
        trovate = [DA_INGLESE[x["name"]] for x in lingue or [] if x.get("name") in DA_INGLESE]
    if not trovate:
        return ""
    posto = {NOMI[c]: i for i, c in enumerate(ordine) if c in NOMI}
    return min(trovate, key=lambda n: posto.get(n, len(posto)))


def titolo_film(film: dict, riserva: str = "") -> str:
    return f"{film.get('title') or riserva} ({film.get('year') or ''})".replace(" ()", "")


def stato_coda(r: dict) -> tuple[str, str]:
    """Lo stato di un download di Radarr/Sonarr detto in parole semplici."""
    if r.get("trackedDownloadStatus") in ("warning", "error") or r.get("status") in ("warning", "failed"):
        messaggi = [m for s in r.get("statusMessages") or [] for m in s.get("messages") or []]
        return "problema", "Problema: " + (messaggi[0] if messaggi else r.get("errorMessage") or "controlla qBittorrent")
    fase = r.get("trackedDownloadState") or ""
    if fase in ("importPending", "importing"):
        return "importazione", "Scaricato: lo sto mettendo in Jellyfin"
    if fase == "importBlocked":
        return "problema", "Scaricato, ma non riesco a metterlo in Jellyfin da solo"
    stato = r.get("status") or ""
    if stato == "delay":
        return "coda", "Trovato: parte tra poco"
    if stato in ("queued", "paused"):
        return "coda", "In coda: parte appena finiscono gli altri"
    if stato == "completed":
        return "importazione", "Scaricato: lo sto mettendo in Jellyfin"
    return "download", "In download"


def secondi(testo: str | None) -> int:
    """Il tempo rimanente di Radarr/Sonarr ("01:02:03" o "1.02:03:04") in secondi."""
    if not testo:
        return 0
    giorni, ore = "0", testo
    if "." in testo.split(":")[0]:
        giorni, ore = testo.split(".", 1)
    try:
        h, m, s = (int(float(x)) for x in ore.split(":"))
        return int(giorni) * 86400 + h * 3600 + m * 60 + s
    except ValueError:
        return 0


def fonte(r: dict) -> str:
    client = (r.get("downloadClient") or "").lower()
    return "Telegram" if "tgarr" in client or "telegram" in client or r.get("protocol") == "usenet" else "Torrent"


def copertina(oggetto: dict | None) -> str:
    """La locandina di un film o di una serie di Radarr/Sonarr, nella misura piccola."""
    for immagine in (oggetto or {}).get("images") or []:
        if immagine.get("coverType") == "poster":
            url = immagine.get("remoteUrl") or ""
            return url.replace("/t/p/original/", "/t/p/w342/")
    return ""


def gruppo_film(film: dict, id_film: int | None) -> dict:
    return {"gruppo": f"film:{id_film or film.get('id')}", "gruppo_titolo": titolo_film(film),
            "poster": copertina(film), "voce": "Film"}


def gruppo_serie(serie: dict | None, id_serie: int | None, voce: str) -> dict:
    serie = serie or {}
    return {"gruppo": f"serie:{id_serie or serie.get('id')}", "gruppo_titolo": serie.get("title") or "Serie",
            "poster": copertina(serie), "voce": voce}


def percentuale(totale: float, mancante: float) -> float:
    return round(100 * (1 - mancante / totale), 1) if totale else 0.0


def episodio(e: dict | None) -> str:
    e = e or {}
    return f"St. {e.get('seasonNumber', 0)} · Ep. {e.get('episodeNumber', 0)}"


class Automatici:
    def __init__(self, config: Config, radarr: Radarr, serie: Serie, qbit=None) -> None:
        self.config = config
        self.qbit = qbit
        self._campioni: dict[str, tuple[float, int]] = {}
        self._velocita_qbit: dict[str, int] = {}
        self.radarr = radarr
        self.serie = serie
        self._attesa: tuple[float, list[dict]] | None = None

    @property
    def preferita(self) -> str:
        return NOMI.get(self.config.lingue_audio[0], "Italiano")

    def _lingua(self, dati: dict) -> str:
        return lingua_da(dati.get("customFormats"), dati.get("languages"), self.config.lingue_audio)

    def _voce(self, titolo: str, dettaglio: str, lingua: str, **altro) -> dict:
        return {
            "titolo": titolo, "dettaglio": dettaglio, "lingua": lingua or "lingua sconosciuta",
            "preferita": lingua == self.preferita, "lingua_preferita": self.preferita, **altro,
        }

    def _velocita(self, r: dict) -> int:
        """Byte al secondo: da qBittorrent se è un torrent, se no da quanto è sceso o dal tempo rimanente."""
        hash_torrent = (r.get("downloadId") or "").lower()
        if hash_torrent in self._velocita_qbit:
            return self._velocita_qbit[hash_torrent]
        mancante = int(r.get("sizeleft") or 0)
        chiave = f"{r.get('downloadId')}|{r.get('id')}"
        adesso = time.monotonic()
        prima = self._campioni.get(chiave)
        self._campioni[chiave] = (adesso, mancante)
        if prima and adesso - prima[0] > 1 and prima[1] > mancante:
            return int((prima[1] - mancante) / (adesso - prima[0]))
        rimanente = secondi(r.get("timeleft"))
        return int(mancante / rimanente) if rimanente and mancante else 0

    def _da_coda(self, r: dict, titolo: str, tipo: str, ids: list[int]) -> dict:
        stato, testo = stato_coda(r)
        velocita = self._velocita(r) if stato == "download" else 0
        mancante = int(r.get("sizeleft") or 0)
        rimanente = secondi(r.get("timeleft")) or (int(mancante / velocita) if velocita else 0)
        return self._voce(titolo, r.get("title", ""), self._lingua(r), tipo=tipo, ids=ids, stato=stato,
                          stato_testo=testo, percentuale=percentuale(r.get("size", 0), r.get("sizeleft", 0)),
                          dimensione=r.get("size", 0), velocita=velocita, rimanente=rimanente, fonte=fonte(r),
                          annullabile=True, ricercabile=False)

    async def _leggi_qbit(self) -> None:
        if self.qbit is None:
            return
        try:
            torrent = await self.qbit.tutti()
            self._velocita_qbit = {t.get("hash", "").lower(): int(t.get("dlspeed") or 0) for t in torrent}
        except Exception:  # noqa: BLE001 - senza qBittorrent la velocità si stima
            self._velocita_qbit = {}

    async def coda(self) -> list[dict]:
        """Tutto quello che Radarr e Sonarr stanno scaricando o cercando, con lo stato in parole semplici."""
        voci: list[dict] = []
        await self._leggi_qbit()
        in_coda_film: set[int] = set()
        in_coda_puntate: set[int] = set()
        try:
            dati = await self.radarr._r("GET", "queue", params={"pageSize": 100, "includeMovie": "true"})
            for r in (dati or {}).get("records", []):
                film = r.get("movie") or {}
                in_coda_film.add(r.get("movieId"))
                voci.append({**self._da_coda(r, titolo_film(film, r.get("title", "")), "film", [r.get("movieId")]),
                             **gruppo_film(film, r.get("movieId"))})
            mancanti = await self.radarr._r("GET", "wanted/missing", params={"pageSize": 100, "monitored": "true"})
            for m in (mancanti or {}).get("records", []):
                if m.get("id") in in_coda_film:
                    continue
                uscito = m.get("isAvailable", True)
                voci.append(self._voce(
                    titolo_film(m), "", "", tipo="film", ids=[m["id"]], stato="cerco" if uscito else "non_uscito",
                    stato_testo=("Cerco una fonte: riprovo da solo appena esce una versione e ogni notte" if uscito
                                 else "Non è ancora uscito: lo scarico appena è disponibile"),
                    percentuale=0, dimensione=0, annullabile=True, ricercabile=uscito, senza_lingua=True,
                    **gruppo_film(m, m.get("id"))))
        except Exception:  # noqa: BLE001 - se Radarr non risponde la pagina mostra il resto
            log.warning("Coda di Radarr non disponibile", exc_info=True)
        if self.serie.attivo:
            try:
                dati = await self.serie._api("GET", "queue", params={
                    "pageSize": 100, "includeSeries": "true", "includeEpisode": "true"})
                for r in (dati or {}).get("records", []):
                    in_coda_puntate.add(r.get("episodeId"))
                    titolo = f"{(r.get('series') or {}).get('title', '')} · {episodio(r.get('episode'))}"
                    voci.append({**self._da_coda(r, titolo, "serie", [r.get("episodeId")]),
                                 **gruppo_serie(r.get("series"), r.get("seriesId"), episodio(r.get("episode")))})
                mancanti = await self.serie._api("GET", "wanted/missing", params={
                    "pageSize": 200, "monitored": "true", "includeSeries": "true"})
                per_serie: dict[int, tuple[dict, list[int]]] = {}
                for e in (mancanti or {}).get("records", []):
                    if e.get("id") not in in_coda_puntate:
                        serie = e.get("series") or {}
                        per_serie.setdefault(e.get("seriesId") or serie.get("id") or 0, (serie, []))[1].append(e["id"])
                for id_serie, (serie, ids) in per_serie.items():
                    quante = "1 puntata" if len(ids) == 1 else f"{len(ids)} puntate"
                    nome = serie.get("title") or "Serie"
                    voci.append(self._voce(
                        f"{nome} · {quante} da trovare", "", "", tipo="serie", ids=ids, stato="cerco",
                        stato_testo="Cerco una fonte: riprovo da solo appena escono e ogni notte",
                        percentuale=0, dimensione=0, annullabile=True, ricercabile=True, senza_lingua=True,
                        **gruppo_serie(serie, id_serie, f"{quante} da trovare")))
            except Exception:  # noqa: BLE001
                log.warning("Coda di Sonarr non disponibile", exc_info=True)
        return voci

    async def annulla(self, tipo: str, ids: list[int]) -> str:
        if tipo == "serie":
            await self.serie.annulla_puntate(ids)
            messaggio = "Annullato: quelle puntate non vengono più cercate né scaricate."
        else:
            messaggio = ""
            for i in ids:
                messaggio = await self.radarr.annulla(i)
        self._attesa = None
        return messaggio

    async def cerca_di_nuovo(self, tipo: str, ids: list[int]) -> str:
        if tipo == "serie":
            await self.serie.scarica_puntate(ids)
        else:
            for i in ids:
                await self.radarr.cerca_di_nuovo(i)
        return "Ricerca avviata: se trovo una versione parte da sola."

    async def stato_film(self, tmdb_id: int) -> dict:
        """Lo stato del film per la sua scheda: niente, cerco, in download, pronto."""
        film = await self.radarr.film(tmdb_id)
        if not film or not film.get("id"):
            return {"stato": "nessuno"}
        coda = await self.radarr._r("GET", "queue/details", params={"movieId": film["id"]}) or []
        if coda:
            await self._leggi_qbit()
            voce = self._da_coda(coda[0], titolo_film(film), "film", [film["id"]])
            return {**voce, "id": film["id"]}
        if film.get("hasFile"):
            lingua = self._lingua(film.get("movieFile") or {})
            return {**self._voce(titolo_film(film), "", lingua, stato="pronto",
                                 stato_testo="Pronto in Jellyfin"), "id": film["id"],
                    "migliorabile": bool(film.get("monitored")) and lingua != self.preferita}
        if film.get("monitored"):
            uscito = film.get("isAvailable", True)
            return {"stato": "cerco" if uscito else "non_uscito", "id": film["id"],
                    "stato_testo": ("Cerco una fonte: riprovo da solo appena esce una versione e ogni notte" if uscito
                                    else "Non è ancora uscito: lo scarico appena è disponibile")}
        return {"stato": "nessuno", "id": film["id"]}

    async def in_attesa(self) -> list[dict]:
        """Già scaricati ma non nella lingua preferita (Radarr/Sonarr li sostituiscono da soli)."""
        if self._attesa is not None and time.monotonic() - self._attesa[0] < CACHE_ATTESA:
            return self._attesa[1]
        voci: list[dict] = []
        try:
            dati = await self.radarr._r("GET", "wanted/cutoff", params={"pageSize": 100})
            for m in (dati or {}).get("records", []):
                file = m.get("movieFile") or {}
                lingua = self._lingua(file)
                titolo = f"{m.get('title', '')} ({m.get('year', '')})".replace(" ()", "")
                voci.append(self._voce(titolo, file.get("relativePath", ""), lingua, tipo="film",
                                       **gruppo_film(m, m.get("id"))))
        except Exception:  # noqa: BLE001
            log.warning("Elenco da migliorare di Radarr non disponibile", exc_info=True)
        if self.serie.attivo:
            try:
                dati = await self.serie._api("GET", "wanted/cutoff", params={
                    "pageSize": 100, "includeSeries": "true", "includeEpisodeFile": "true"})
                for e in (dati or {}).get("records", []):
                    file = e.get("episodeFile") or {}
                    titolo = f"{(e.get('series') or {}).get('title', '')} · {episodio(e)}"
                    voci.append(self._voce(titolo, file.get("relativePath", ""), self._lingua(file), tipo="serie",
                                           **gruppo_serie(e.get("series"), e.get("seriesId"), episodio(e))))
            except Exception:  # noqa: BLE001
                log.warning("Elenco da migliorare di Sonarr non disponibile", exc_info=True)
        voci = [v for v in voci if not v["preferita"]]
        self._attesa = (time.monotonic(), voci)
        return voci

    async def cerca_tutto(self) -> None:
        await self.radarr._r("POST", "command", json={"name": "CutoffUnmetMoviesSearch"})
        await self.radarr._r("POST", "command", json={"name": "MissingMoviesSearch"})
        if self.serie.attivo:
            await self.serie._api("POST", "command", json={"name": "CutoffUnmetEpisodeSearch"})
            await self.serie._api("POST", "command", json={"name": "MissingEpisodeSearch"})
        self._attesa = None

    async def ricerca_notturna(self) -> None:
        while True:
            adesso = dt.datetime.now()
            prossima = adesso.replace(hour=ORA_RICERCA, minute=0, second=0, microsecond=0)
            if prossima <= adesso:
                prossima += dt.timedelta(days=1)
            await asyncio.sleep((prossima - adesso).total_seconds())
            try:
                await self.cerca_tutto()
                log.info("Ricerca notturna avviata: versioni migliori e quelli mancanti")
            except Exception:  # noqa: BLE001 - riproviamo la notte dopo
                log.exception("Ricerca notturna non riuscita")
