"""Avvisi quando Radarr e Sonarr trovano da soli la versione nella lingua preferita.

Ogni pochi minuti si legge la cronologia di Radarr e Sonarr (solo gli eventi nuovi) e si
avvisa, sul telefono con ntfy e nella pagina Download:

- "Trovata in italiano": è partito il download di una versione in italiano;
- "Pronta in italiano": la versione in italiano è in Jellyfin;
- "Ora in italiano": ha sostituito una versione in un'altra lingua;
- e, da controlla_tracce (automatici.py), i MULTi che nelle tracce audio hanno l'italiano.

Le puntate della stessa serie vanno in un avviso solo. I download avviati da Cerca hanno
già il loro avviso ("Pronto da guardare"): qui si saltano.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from .automatici import NOMI, lingua_da, titolo_film

log = logging.getLogger("cerca.avvisi_italiano")

OGNI = 300
CHIAVE_ULTIMO = "avvisi_italiano:ultimo:"
CHIAVE_ELENCO = "avvisi_italiano:elenco"
MAX_ELENCO = 30
GIORNI_IN_PAGINA = 7
MAX_PAGINE = 5
PER_PAGINA = 100


def puntate_testo(coppie) -> str:
    """[(1, 3), (1, 4), (1, 5), (1, 7), (2, 1)] → "St. 1 Ep. 3-5, 7 · St. 2 Ep. 1"."""
    per_stagione: dict[int, list[int]] = {}
    for stagione, numero in sorted(set(coppie)):
        per_stagione.setdefault(stagione, []).append(numero)
    parti = []
    for stagione, numeri in per_stagione.items():
        tratti: list[list[int]] = []
        for n in numeri:
            if tratti and n == tratti[-1][1] + 1:
                tratti[-1][1] = n
            else:
                tratti.append([n, n])
        testo = ", ".join(f"{a}" if a == b else f"{a}-{b}" for a, b in tratti)
        parti.append(f"St. {stagione} Ep. {testo}")
    return " · ".join(parti)


def _altre(lingue: set[str]) -> str:
    lingue = {x for x in lingue if x}
    return f"in {next(iter(lingue)).lower()}" if len(lingue) == 1 else "in un'altra lingua"


class AvvisiItaliano:
    def __init__(self, config, archivio, radarr, serie, notifica=None) -> None:
        self.config = config
        self.archivio = archivio
        self.radarr = radarr
        self.serie = serie
        self.notifica = notifica  # async (titolo, messaggio, link) -> bool

    @property
    def preferita(self) -> str:
        return NOMI.get(self.config.lingue_audio[0], "Italiano")

    def _lingua(self, r: dict) -> str:
        return lingua_da(r.get("customFormats"), r.get("languages"), self.config.lingue_audio)

    def _di_cerca(self, r: dict) -> bool:
        hash_torrent = (r.get("downloadId") or "").lower()
        return bool(hash_torrent) and self.archivio.download_per_hash(hash_torrent) is not None

    # --- cronologia ---

    async def _nuovi(self, nome: str, chiama, params: dict) -> list[dict]:
        """Gli eventi della cronologia dopo l'ultimo visto. Al primo giro si segna dove si è, senza avvisare."""
        salvato = self.archivio.stato_leggi(CHIAVE_ULTIMO + nome)
        primo = salvato is None
        ultimo = int(salvato or 0)
        nuovi: list[dict] = []
        for pagina in range(1, MAX_PAGINE + 1):
            dati = await chiama("GET", "history", params={
                **params, "page": pagina, "pageSize": PER_PAGINA, "sortKey": "date", "sortDirection": "descending"}) or {}
            record = dati.get("records") or []
            nuovi += [r for r in record if int(r.get("id") or 0) > ultimo]
            if primo or not record or any(int(r.get("id") or 0) <= ultimo for r in record):
                break
        massimo = max([ultimo] + [int(r.get("id") or 0) for r in nuovi])
        if primo or massimo != ultimo:
            self.archivio.stato_scrivi(CHIAVE_ULTIMO + nome, str(massimo))
        return [] if primo else nuovi

    def _classifica(self, record: list[dict], chiave) -> dict:
        """chiave(record) → (gruppo, elemento).

        Restituisce {gruppo: {"trovata"|"pronta"|"sostituita": {elemento: lingua vecchia}}}."""
        preferita = self.preferita
        importati, presi, sostituiti = {}, {}, {}
        for r in sorted(record, key=lambda x: int(x.get("id") or 0)):
            k = chiave(r)
            if k is None:
                continue
            evento = r.get("eventType") or ""
            if evento in ("episodeFileDeleted", "movieFileDeleted"):
                if ((r.get("data") or {}).get("reason") or "").lower() == "upgrade":
                    sostituiti[k] = self._lingua(r)
            elif self._di_cerca(r) or self._lingua(r) != preferita:
                continue
            elif evento == "downloadFolderImported":
                importati[k] = r
            elif evento == "grabbed":
                presi[k] = r
        gruppi: dict = {}
        for k in importati:
            if k in sostituiti:
                if sostituiti[k] == preferita:
                    continue  # solo una qualità migliore, sempre in italiano: niente di nuovo
                gruppi.setdefault(k[0], {}).setdefault("sostituita", {})[k[1]] = sostituiti[k]
            else:
                gruppi.setdefault(k[0], {}).setdefault("pronta", {})[k[1]] = ""
        for k in presi:
            if k not in importati:
                gruppi.setdefault(k[0], {}).setdefault("trovata", {})[k[1]] = ""
        return gruppi

    def _messaggi(self, nome: str, gruppi_tipo: dict, quali: str) -> list[tuple[str, str, str]]:
        """(tipo, titolo, messaggio) per un film o una serie."""
        lingua = self.preferita.lower()
        fuori = []
        if "trovata" in gruppi_tipo:
            fuori.append(("trovata", f"Trovata in {lingua}", f"{nome}{quali(gruppi_tipo['trovata'])}: in download."))
        if "pronta" in gruppi_tipo:
            fuori.append(("pronta", f"Pronta in {lingua}", f"{nome}{quali(gruppi_tipo['pronta'])}: in Jellyfin."))
        if "sostituita" in gruppi_tipo:
            vecchie = _altre(set(gruppi_tipo["sostituita"].values()))
            fuori.append(("sostituita", f"Ora in {lingua}",
                          f"{nome}{quali(gruppi_tipo['sostituita'])}: ho sostituito la versione {vecchie}."))
        return fuori

    async def giro(self) -> int:
        """Un controllo della cronologia. Restituisce quanti avvisi ha mandato."""
        avvisi: list[tuple[str, str, str, str]] = []
        try:
            record = await self._nuovi("radarr", self.radarr._r, {"includeMovie": "true"})
            film = {r.get("movieId"): r.get("movie") or {} for r in record}
            chiave_film = lambda r: (r.get("movieId"), 0) if r.get("movieId") else None  # noqa: E731
            for id_film, tipi in self._classifica(record, chiave_film).items():
                nome = titolo_film(film.get(id_film) or {}, "Film")
                avvisi += [(t, ti, m, "film") for t, ti, m in self._messaggi(nome, tipi, lambda _: "")]
        except Exception:  # noqa: BLE001 - Radarr spento: si riprova al giro dopo
            log.warning("Cronologia di Radarr non disponibile", exc_info=True)
        if self.serie.attivo:
            try:
                record = await self._nuovi("sonarr", self.serie._api, {"includeSeries": "true", "includeEpisode": "true"})
                serie = {r.get("seriesId"): r.get("series") or {} for r in record}
                puntate = {r.get("episodeId"): r.get("episode") or {} for r in record}

                def quali(elementi: dict) -> str:
                    return " " + puntate_testo((puntate.get(e, {}).get("seasonNumber", 0),
                                                puntate.get(e, {}).get("episodeNumber", 0)) for e in elementi)

                chiave = lambda r: (r.get("seriesId"), r.get("episodeId")) if r.get("episodeId") else None  # noqa: E731
                for id_serie, tipi in self._classifica(record, chiave).items():
                    nome = (serie.get(id_serie) or {}).get("title") or "Serie"
                    avvisi += [(t, ti, m, "serie") for t, ti, m in self._messaggi(nome, tipi, quali)]
            except Exception:  # noqa: BLE001
                log.warning("Cronologia di Sonarr non disponibile", exc_info=True)
        for tipo, titolo, messaggio, _ in avvisi:
            await self._avvisa(tipo, titolo, messaggio)
        return len(avvisi)

    async def multi_confermati(self, film: list[dict], puntate: list[dict]) -> None:
        """I MULTi senza lingue scritte che nelle tracce audio hanno la lingua preferita."""
        lingua = self.preferita.lower()
        for m in film:
            await self._avvisa("pronta", f"Pronta in {lingua}",
                               f"{titolo_film(m, 'Film')}: era un MULTI, l'audio in {lingua} c'è.")
        per_serie: dict[int, tuple[str, list[tuple[int, int]]]] = {}
        for e in puntate:
            nome = (e.get("series") or {}).get("title") or "Serie"
            per_serie.setdefault(e.get("seriesId") or 0, (nome, []))[1].append(
                (e.get("seasonNumber", 0), e.get("episodeNumber", 0)))
        for nome, coppie in per_serie.values():
            await self._avvisa("pronta", f"Pronta in {lingua}",
                               f"{nome} {puntate_testo(coppie)}: era un MULTI, l'audio in {lingua} c'è.")

    # --- invio ed elenco nella pagina ---

    async def _avvisa(self, tipo: str, titolo: str, messaggio: str) -> None:
        log.info("%s: %s", titolo, messaggio)
        elenco = self.elenco()
        elenco.insert(0, {"quando": time.time(), "tipo": tipo, "titolo": titolo, "messaggio": messaggio, "visto": False})
        self.archivio.stato_scrivi(CHIAVE_ELENCO, json.dumps(elenco[:MAX_ELENCO]))
        if self.notifica:
            dove = "/jellyfin/" if tipo != "trovata" else f"{self.config.root_path}/download"
            try:
                await self.notifica(titolo, messaggio, f"{self.config.indirizzo_pubblico}{dove}")
            except Exception:  # noqa: BLE001 - l'avviso resta comunque nella pagina
                log.warning("Notifica non inviata", exc_info=True)

    def elenco(self) -> list[dict]:
        try:
            dati = json.loads(self.archivio.stato_leggi(CHIAVE_ELENCO) or "[]")
        except ValueError:
            return []
        return dati if isinstance(dati, list) else []

    def recenti(self) -> list[dict]:
        """Quelli da mostrare nella pagina Download: degli ultimi giorni e non ancora visti."""
        limite = time.time() - GIORNI_IN_PAGINA * 86400
        return [a for a in self.elenco() if not a.get("visto") and a.get("quando", 0) >= limite]

    def segna_visti(self) -> None:
        self.archivio.stato_scrivi(CHIAVE_ELENCO, json.dumps([{**a, "visto": True} for a in self.elenco()]))

    async def ciclo(self) -> None:
        while True:
            try:
                await self.giro()
            except Exception:  # noqa: BLE001 - un errore non deve fermare gli avvisi per sempre
                log.exception("Errore negli avvisi della lingua preferita")
            await asyncio.sleep(OGNI)
