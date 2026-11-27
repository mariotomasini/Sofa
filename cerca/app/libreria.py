"""Quello che c'è sul disco (film, serie, anime, altro) e la sua eliminazione a mano dalla pagina Spazio.

Eliminare vuol dire toglierlo ovunque:
- film: via da Radarr con i file (non lo segue più, non lo riscarica);
- serie: via da Sonarr con i file (non è più tra le seguite), via i link diretti salvati
  (AnimeUnity, AnimeWorld, StreamingCommunity) e i download di Cerca ancora in corso;
- stagione o puntata: via i file, e quelle puntate non si seguono più (non tornano);
- altro: cartelle nelle librerie che né Radarr né Sonarr conoscono (copiate a mano), via dal disco.
In più si tolgono i torrent ancora in condivisione con quei file, così lo spazio si libera
davvero, e Jellyfin aggiorna la libreria, così sparisce anche lì.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
from pathlib import Path

import httpx

from .config import Config
from .db import Archivio
from .foto_protette import tocca_le_foto
from .pulizia import dimensione
from .rete import ErroreServizio, non_raggiungibile

log = logging.getLogger("cerca.libreria")

MOTIVO = "eliminato a mano dalla pagina Spazio"
LIBRERIE = ("film", "serie", "anime")


# --- funzioni pure (testate) ---------------------------------------------------------

def _poster(voce: dict) -> str:
    return next((i.get("remoteUrl") or "" for i in voce.get("images") or [] if i.get("coverType") == "poster"), "")


def riassumi_film(film: list[dict]) -> list[dict]:
    """I film di Radarr con un file sul disco, dal più pesante."""
    risultato = []
    for f in film:
        file = f.get("movieFile") or {}
        peso = f.get("sizeOnDisk") or file.get("size") or 0
        if not f.get("hasFile") and not file:
            continue
        risultato.append({"id": f["id"], "titolo": f.get("title", ""), "anno": str(f.get("year") or ""),
                          "poster": _poster(f), "dimensione": peso, "percorso": f.get("path", "")})
    return sorted(risultato, key=lambda x: -x["dimensione"])


def riassumi_serie(serie: list[dict]) -> list[dict]:
    """Le serie di Sonarr con almeno una puntata sul disco, con le stagioni, dalla più pesante."""
    risultato = []
    for s in serie:
        stat = s.get("statistics") or {}
        if not stat.get("episodeFileCount"):
            continue
        stagioni = [{"numero": st.get("seasonNumber", 0),
                     "puntate": (st.get("statistics") or {}).get("episodeFileCount", 0),
                     "dimensione": (st.get("statistics") or {}).get("sizeOnDisk", 0)}
                    for st in s.get("seasons") or [] if (st.get("statistics") or {}).get("episodeFileCount")]
        risultato.append({"id": s["id"], "titolo": s.get("title", ""), "anno": str(s.get("year") or ""),
                          "poster": _poster(s), "dimensione": stat.get("sizeOnDisk", 0),
                          "puntate": stat.get("episodeFileCount", 0), "anime": s.get("seriesType") == "anime",
                          "seguita": bool(s.get("monitored")), "percorso": s.get("path", ""),
                          "stagioni": sorted(stagioni, key=lambda x: x["numero"])})
    return sorted(risultato, key=lambda x: -x["dimensione"])


def puntate_per_stagione(episodi: list[dict], file: list[dict]) -> list[dict]:
    """I file di una serie divisi per stagione, con il nome delle puntate che contengono."""
    per_file: dict[int, list[dict]] = {}
    for e in episodi:
        if e.get("episodeFileId"):
            per_file.setdefault(e["episodeFileId"], []).append(e)
    stagioni: dict[int, list[dict]] = {}
    for f in file:
        dentro = sorted(per_file.get(f["id"], []), key=lambda e: e.get("episodeNumber", 0))
        numeri = "-".join(str(e.get("episodeNumber", 0)) for e in dentro) or "?"
        stagioni.setdefault(f.get("seasonNumber", 0), []).append({
            "file_id": f["id"], "numero": numeri,
            "ordine": dentro[0].get("episodeNumber", 0) if dentro else 0,
            "titolo": " / ".join(e.get("title", "") for e in dentro if e.get("title")),
            "dimensione": f.get("size", 0), "nome_file": Path(f.get("relativePath") or f.get("path") or "").name,
        })
    return [{"numero": n, "puntate": sorted(p, key=lambda x: x["ordine"]),
             "dimensione": sum(x["dimensione"] for x in p)} for n, p in sorted(stagioni.items())]


def _pulito(percorso: str) -> str:
    return str(percorso).rstrip("/")


def estranei(cartelle: list[Path], noti: set[str]) -> list[Path]:
    """Le voci dentro le librerie che né Radarr né Sonarr conoscono (file o cartelle copiati a mano)."""
    noti = {_pulito(n) for n in noti}
    risultato = []
    for cartella in cartelle:
        if not cartella.is_dir():
            continue
        for voce in sorted(cartella.iterdir()):
            if voce.name.startswith(".") or _pulito(voce) in noti:
                continue
            risultato.append(voce)
    return risultato


def dentro_una_libreria(percorso: Path, cartelle: list[Path]) -> bool:
    """Vero solo per una voce che sta direttamente dentro una delle librerie (mai la libreria stessa)."""
    if ".." in percorso.parts:
        return False
    return any(percorso.parent == c for c in cartelle) and percorso.name not in ("", ".", "..")


def torrent_da_togliere(storia: list[dict], tolte: set[int] | None) -> set[str]:
    """Gli hash dei torrent dalla cronologia di Radarr/Sonarr.

    Con `tolte` (puntate eliminate) solo i torrent che contenevano unicamente quelle puntate:
    un pacchetto con altre puntate ancora sul disco resta in condivisione.
    """
    per_torrent: dict[str, set[int]] = {}
    for voce in storia:
        hash_t = (voce.get("downloadId") or "").lower()
        if hash_t:
            per_torrent.setdefault(hash_t, set())
            if voce.get("episodeId"):
                per_torrent[hash_t].add(voce["episodeId"])
    if tolte is None:
        return set(per_torrent)
    return {h for h, puntate in per_torrent.items() if puntate and puntate <= tolte}


# --- gestore ---------------------------------------------------------------------------

class Libreria:
    def __init__(self, config: Config, archivio: Archivio, client: httpx.AsyncClient,
                 qbit, jellyfin, download, serie) -> None:
        self.config = config
        self.archivio = archivio
        self.client = client
        self.qbit = qbit
        self.jellyfin = jellyfin
        self.download = download
        self.serie = serie
        self._lock = asyncio.Lock()

    @property
    def cartelle(self) -> list[Path]:
        base = Path(self.config.cartella_film_radarr).parent
        return [base / nome for nome in LIBRERIE]

    async def _radarr(self, metodo: str, percorso: str, **kw):
        try:
            r = await self.client.request(metodo, f"{self.config.radarr_url}/api/v3/{percorso}",
                                          headers={"X-Api-Key": self.config.radarr_chiave}, timeout=60, **kw)
        except httpx.HTTPError as exc:
            raise non_raggiungibile("Radarr", "/radarr") from exc
        if not r.is_success:
            raise ErroreServizio(f"Radarr ha risposto con un errore ({r.status_code}).", "Riprova tra poco.")
        return r.json() if r.content else None

    async def _sonarr(self, metodo: str, percorso: str, **kw):
        if not self.serie.attivo:
            raise ErroreServizio("Le serie TV non sono attive.", "Accendile nelle impostazioni di mediabox.")
        return await self.serie._api(metodo, percorso, **kw)

    # --- elenco ------------------------------------------------------------------------
    async def elenco(self) -> dict:
        errori: list[str] = []
        film_radarr: list[dict] = []
        serie_sonarr: list[dict] = []
        try:
            film_radarr = await self._radarr("GET", "movie") or []
        except ErroreServizio as exc:
            errori.append(exc.cosa)
        if self.serie.attivo:
            try:
                serie_sonarr = await self._sonarr("GET", "series") or []
            except ErroreServizio as exc:
                errori.append(exc.cosa)
        altro: list[dict] = []
        # senza l'elenco di Radarr o Sonarr non si sa cosa è "estraneo": meglio non mostrare niente
        if not errori:
            noti = {f.get("path", "") for f in film_radarr} | {s.get("path", "") for s in serie_sonarr}
            for voce in await asyncio.to_thread(estranei, self.cartelle, noti):
                altro.append({"percorso": str(voce), "nome": voce.name, "libreria": voce.parent.name,
                              "dimensione": await asyncio.to_thread(dimensione, voce)})
            altro.sort(key=lambda x: -x["dimensione"])
        return {"film": riassumi_film(film_radarr), "serie": riassumi_serie(serie_sonarr),
                "altro": altro, "errori": errori}

    async def puntate(self, serie_id: int) -> list[dict]:
        episodi, file = await asyncio.gather(self._sonarr("GET", "episode", params={"seriesId": serie_id}),
                                             self._sonarr("GET", "episodefile", params={"seriesId": serie_id}))
        return puntate_per_stagione(episodi or [], file or [])

    # --- pezzi comuni ------------------------------------------------------------------
    async def _togli_torrent(self, hash_torrent: set[str]) -> None:
        if not hash_torrent:
            return
        try:
            presenti = {t.get("hash", "").lower() for t in await self.qbit.tutti()}
        except ErroreServizio:
            return
        for h in hash_torrent & presenti:
            with contextlib.suppress(ErroreServizio):
                await self.qbit.elimina(h, con_file=True)

    async def _togli_download_cerca(self, voci: list[dict]) -> None:
        """I download di Cerca: quelli in corso si annullano (file parziali compresi), gli altri si tolgono dall'elenco."""
        for v in voci:
            if v["stato"] != "importato":
                with contextlib.suppress(ErroreServizio):
                    await self.download.annulla(v["id"])
                continue
            if v.get("hash"):
                with contextlib.suppress(ErroreServizio):
                    await self.qbit.elimina(v["hash"], con_file=True)
            self.archivio.elimina_download(v["id"])

    async def _fine(self, cosa: str, peso: int) -> dict:
        self.archivio.registra_pulizia(cosa, peso, MOTIVO)
        await self.jellyfin.aggiorna_libreria()
        return {"messaggio": f"Eliminato: {cosa}. Sparisce anche da Jellyfin tra poco.", "liberati": peso}

    # --- eliminazione ------------------------------------------------------------------
    async def elimina_film(self, film_id: int) -> dict:
        async with self._lock:
            film = await self._radarr("GET", f"movie/{film_id}")
            if not film:
                raise ErroreServizio("Non trovo più questo film in Radarr.", "Ricarica la pagina.")
            peso = film.get("sizeOnDisk") or (film.get("movieFile") or {}).get("size") or 0
            storia = []
            with contextlib.suppress(ErroreServizio):
                storia = await self._radarr("GET", "history/movie", params={"movieId": film_id}) or []
            await self._radarr("DELETE", f"movie/{film_id}",
                               params={"deleteFiles": "true", "addImportExclusion": "false"})
            await self._togli_torrent(torrent_da_togliere(storia, None))
            if film.get("tmdbId"):
                await self._togli_download_cerca(self.archivio.download_del_film(film["tmdbId"]))
            anno = f" ({film['year']})" if film.get("year") else ""
            return await self._fine(f"Film {film.get('title', '')}{anno}", peso)

    async def elimina_serie(self, serie_id: int) -> dict:
        async with self._lock:
            serie = await self._sonarr("GET", f"series/{serie_id}")
            peso = (serie.get("statistics") or {}).get("sizeOnDisk", 0)
            storia = []
            with contextlib.suppress(ErroreServizio):
                storia = await self._sonarr("GET", "history/series", params={"seriesId": serie_id}) or []
            # prima i download di Cerca (link diretti), così non ripartono mentre la serie sparisce
            await self._togli_download_cerca(self.archivio.download_della_serie(serie_id))
            await self._sonarr("DELETE", f"series/{serie_id}",
                               params={"deleteFiles": "true", "addImportListExclusion": "false"})
            for chiave in (f"animeunity:{serie_id}", f"titoli_serie:{serie_id}"):
                self.archivio.stato_togli(chiave)
            await self._togli_torrent(torrent_da_togliere(storia, None))
            return await self._fine(f"Serie {serie.get('title', '')} (tutta)", peso)

    async def _elimina_file(self, serie_id: int, file_ids: list[int]) -> tuple[int, set[int]]:
        """Toglie i file e smette di seguire le loro puntate. Restituisce (byte, puntate tolte)."""
        episodi = await self._sonarr("GET", "episode", params={"seriesId": serie_id}) or []
        file = {f["id"]: f for f in await self._sonarr("GET", "episodefile", params={"seriesId": serie_id}) or []}
        scelti = [i for i in file_ids if i in file]
        if not scelti:
            raise ErroreServizio("Non trovo più questi file in Sonarr.", "Ricarica la pagina.")
        tolte = {e["id"] for e in episodi if e.get("episodeFileId") in scelti}
        if tolte:
            await self._sonarr("PUT", "episode/monitor", json={"episodeIds": sorted(tolte), "monitored": False})
        # un download diretto ancora in corso di quelle puntate si ferma
        for id_puntata, d in self.archivio.download_puntate(serie_id).items():
            if id_puntata in tolte:
                with contextlib.suppress(ErroreServizio):
                    await self.download.annulla(d["id"])
        for i in scelti:
            await self._sonarr("DELETE", f"episodefile/{i}")
        return sum(file[i].get("size", 0) for i in scelti), tolte

    async def _torrent_puntate(self, serie_id: int, tolte: set[int]) -> None:
        with contextlib.suppress(ErroreServizio):
            storia = await self._sonarr("GET", "history/series", params={"seriesId": serie_id}) or []
            await self._togli_torrent(torrent_da_togliere(storia, tolte))

    async def elimina_stagione(self, serie_id: int, stagione: int) -> dict:
        async with self._lock:
            serie = await self._sonarr("GET", f"series/{serie_id}")
            file = await self._sonarr("GET", "episodefile", params={"seriesId": serie_id}) or []
            peso, tolte = await self._elimina_file(
                serie_id, [f["id"] for f in file if f.get("seasonNumber") == stagione])
            # la stagione non si segue più: Sonarr non la riscarica
            for st in serie.get("seasons") or []:
                if st.get("seasonNumber") == stagione:
                    st["monitored"] = False
            serie.pop("statistics", None)
            await self._sonarr("PUT", f"series/{serie_id}", json=serie)
            await self._torrent_puntate(serie_id, tolte)
            nome = "Speciali" if stagione == 0 else f"stagione {stagione}"
            return await self._fine(f"{serie.get('title', '')}, {nome}", peso)

    async def elimina_puntata(self, serie_id: int, file_id: int) -> dict:
        async with self._lock:
            serie = await self._sonarr("GET", f"series/{serie_id}")
            episodi = await self._sonarr("GET", "episode", params={"seriesId": serie_id}) or []
            dentro = sorted((e for e in episodi if e.get("episodeFileId") == file_id),
                            key=lambda e: (e.get("seasonNumber", 0), e.get("episodeNumber", 0)))
            peso, tolte = await self._elimina_file(serie_id, [file_id])
            await self._torrent_puntate(serie_id, tolte)
            etichetta = ", ".join(f"{e.get('seasonNumber', 0)}x{e.get('episodeNumber', 0):02d}" for e in dentro)
            return await self._fine(f"{serie.get('title', '')}, puntata {etichetta or '?'}", peso)

    async def elimina_altro(self, percorso: str) -> dict:
        async with self._lock:
            voce = Path(percorso)
            if tocca_le_foto(voce) or not dentro_una_libreria(voce, self.cartelle):
                raise ErroreServizio("Questo percorso non è nelle librerie di mediabox.", "Ricarica la pagina.")
            elenco = await self.elenco()
            if elenco["errori"] or percorso not in {a["percorso"] for a in elenco["altro"]}:
                raise ErroreServizio("Non posso eliminarlo: Radarr o Sonarr lo conoscono, o non c'è più.",
                                     "Ricarica la pagina.")
            peso = await asyncio.to_thread(dimensione, voce)
            try:
                if voce.is_dir() and not voce.is_symlink():
                    await asyncio.to_thread(shutil.rmtree, voce)
                else:
                    await asyncio.to_thread(voce.unlink)
            except OSError as exc:
                log.warning("Non riesco a cancellare %s: %s", voce, exc)
                raise ErroreServizio("Non riesco a cancellarlo dal disco.",
                                     "Forse lo sta usando un altro programma: riprova tra poco.") from exc
            return await self._fine(f"{voce.name} (cartella {voce.parent.name})", peso)
