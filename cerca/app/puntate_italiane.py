"""Le puntate nuove delle serie seguite, cercate anche con il titolo italiano.

Sonarr cerca e riconosce i torrent solo con il titolo inglese: "Mare Fuori S05E01 iTALiAN"
per lui non è *The Sea Beyond*, e la puntata resta "da trovare". Ogni tanto Cerca prende le
puntate già uscite che Sonarr non ha trovato, le cerca su Prowlarr con il titolo italiano,
quello originale e quelli di Sonarr, e scarica la versione nella lingua preferita. A download
finito la consegna a Sonarr (download.py), come faceva il vecchio pulsante "Cerca le puntate".
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time

from .automatici import tutte_le_pagine
from .lingue_release import lingue_audio

log = logging.getLogger("cerca.puntate_italiane")

OGNI = 3 * 3600  # le puntate escono a orari diversi: un giro ogni tre ore basta
MAX_PER_GIRO = 8  # torrent avviati al massimo in un giro, per non riempire la coda
MAX_SERIE_PER_GIRO = 15  # serie cercate in un giro (ognuna sono fino a 4 ricerche su Prowlarr)
ORE_ATTESA = 24  # una puntata già avviata da qui non si riprova prima di un giorno
MAX_TENTATI = 500
CHIAVE_TITOLI = "titoli_serie:"
CHIAVE_STATO = "puntate_italiane"

RE_EPISODIO = re.compile(r"Stagione (\d+) · Episodio (\d+)")
RE_STAGIONE = re.compile(r"Stagione (\d+)$")


def ricorda_titoli(archivio, serie_id: int, titoli: list[str]) -> None:
    """Il titolo italiano e quello originale della serie, come li mostra Cerca (Sonarr ha solo l'inglese)."""
    nuovi = [t.strip() for t in titoli if t and t.strip()]
    if not serie_id or not nuovi:
        return
    vecchi = titoli_ricordati(archivio, serie_id)
    archivio.stato_scrivi(f"{CHIAVE_TITOLI}{serie_id}", json.dumps(list(dict.fromkeys(nuovi + vecchi))[:4]))


def titoli_ricordati(archivio, serie_id: int) -> list[str]:
    try:
        return json.loads(archivio.stato_leggi(f"{CHIAVE_TITOLI}{serie_id}") or "[]")
    except ValueError:
        return []


def scegli(risultati: list[dict], mancanti: list[tuple[int, int, int]], lingua: str,
           tentati: set[str]) -> list[tuple[dict, list[int]]]:
    """Quali torrent scaricare: [(risultato, id delle puntate che contiene)].

    `risultati` sono già in ordine dal migliore (Prowlarr.cerca_serie); `mancanti` sono
    (id puntata, stagione, numero). Solo torrent con abbastanza fonti e con l'audio nella
    lingua preferita. Se di una stagione mancano più puntate va bene anche la stagione intera.
    """
    buoni = [r for r in risultati
             if not r.get("poche_fonti") and r.get("protocollo") == "torrent"
             and r.get("id") not in tentati and lingua in lingue_audio(r.get("titolo", ""))]
    per_stagione: dict[int, dict[int, int]] = {}
    for id_puntata, stagione, numero in mancanti:
        per_stagione.setdefault(stagione, {})[numero] = id_puntata
    scelti: list[tuple[dict, list[int]]] = []
    for stagione, puntate in sorted(per_stagione.items()):
        if len(puntate) > 1:
            pacchetto = next((r for r in buoni if (m := RE_STAGIONE.match(r.get("contenuto", "")))
                              and int(m.group(1)) == stagione), None)
            if pacchetto:
                scelti.append((pacchetto, list(puntate.values())))
                continue
        for numero, id_puntata in sorted(puntate.items()):
            singola = next((r for r in buoni if (m := RE_EPISODIO.match(r.get("contenuto", "")))
                            and (int(m.group(1)), int(m.group(2))) == (stagione, numero)), None)
            if singola:
                scelti.append((singola, [id_puntata]))
    return scelti


class PuntateItaliane:
    def __init__(self, config, archivio, serie, prowlarr, download) -> None:
        self.config = config
        self.archivio = archivio
        self.serie = serie
        self.prowlarr = prowlarr
        self.download = download

    @property
    def lingua(self) -> str:
        return (self.config.lingue_audio or ("it",))[0]

    def _stato(self) -> dict:
        try:
            stato = json.loads(self.archivio.stato_leggi(CHIAVE_STATO) or "{}")
        except ValueError:
            stato = {}
        return {"avviate": stato.get("avviate") or {}, "tentati": stato.get("tentati") or []}

    def _salva(self, stato: dict) -> None:
        limite = time.time() - ORE_ATTESA * 3600
        stato["avviate"] = {k: v for k, v in stato["avviate"].items() if v >= limite}
        stato["tentati"] = stato["tentati"][-MAX_TENTATI:]
        self.archivio.stato_scrivi(CHIAVE_STATO, json.dumps(stato))

    async def _mancanti(self, avviate: dict) -> dict[int, tuple[dict, list[tuple[int, int, int]]]]:
        """Le puntate già uscite che Sonarr cerca e non ha in coda, per serie."""
        coda = await tutte_le_pagine(self.serie._api, "queue", {})
        in_coda = {r.get("episodeId") for r in coda}
        mancanti = await tutte_le_pagine(self.serie._api, "wanted/missing", {
            "monitored": "true", "includeSeries": "true"})
        per_serie: dict[int, tuple[dict, list[tuple[int, int, int]]]] = {}
        for e in mancanti:
            id_puntata = e.get("id")
            if (not id_puntata or id_puntata in in_coda or str(id_puntata) in avviate
                    or not e.get("seasonNumber")):  # gli speciali (stagione 0) hanno nomi a caso
                continue
            serie = e.get("series") or {}
            id_serie = e.get("seriesId") or serie.get("id")
            if id_serie:
                per_serie.setdefault(id_serie, (serie, []))[1].append(
                    (id_puntata, e["seasonNumber"], e.get("episodeNumber") or 0))
        return per_serie

    async def giro(self) -> int:
        """Un controllo: quanti torrent ha avviato."""
        if not self.serie.attivo or not self.config.prowlarr_chiave:
            return 0
        stato = self._stato()
        per_serie = await self._mancanti(stato["avviate"])
        avviati = 0
        # prima le serie con meno puntate mancanti: di solito quelle in corso, con la puntata nuova
        for id_serie, (serie, mancanti) in sorted(per_serie.items(), key=lambda x: len(x[1][1]))[:MAX_SERIE_PER_GIRO]:
            if avviati >= MAX_PER_GIRO:
                break
            titoli = [*titoli_ricordati(self.archivio, id_serie), serie.get("title", ""),
                      *(a.get("title", "") for a in serie.get("alternateTitles") or [])]
            try:
                risposta = await self.prowlarr.cerca_serie(id_serie, titoli)
            except Exception:  # noqa: BLE001 - Prowlarr lento o un indexer giù: si riprova al prossimo giro
                log.debug("Ricerca puntate di %s non riuscita", serie.get("title"), exc_info=True)
                continue
            for risultato, puntate in scegli(risposta.get("risultati") or [], mancanti, self.lingua,
                                             set(stato["tentati"])):
                if avviati >= MAX_PER_GIRO:
                    break
                if await self._scarica(id_serie, serie, risultato):
                    avviati += 1
                    stato["avviate"].update({str(p): time.time() for p in puntate})
                stato["tentati"].append(risultato["id"])
        self._salva(stato)
        if avviati:
            log.info("Puntate col titolo italiano: avviati %s download", avviati)
        return avviati

    async def _scarica(self, id_serie: int, serie: dict, risultato: dict) -> bool:
        try:
            originale = await asyncio.to_thread(self.prowlarr.risultato_originale, id_serie, risultato["id"], "serie")
            magnet, file_torrent = await self.prowlarr.scarica_torrent(originale)
            nome = titoli_ricordati(self.archivio, id_serie)[:1] or [serie.get("title", "")]
            poster = next((i.get("remoteUrl") for i in serie.get("images") or [] if i.get("coverType") == "poster"), "")
            await self.download.da_torrent(
                fonte="prowlarr", categoria="cerca", film=None,
                titolo=f"{nome[0]} · {risultato.get('contenuto') or risultato.get('titolo', '')}",
                magnet=magnet, file_torrent=file_torrent, serie=id_serie, poster=poster or "",
            )
            return True
        except Exception:  # noqa: BLE001 - quel torrent non si riprova, gli altri sì
            log.warning("Download di %s non avviato", risultato.get("titolo"), exc_info=True)
            return False

    async def ciclo(self) -> None:
        while True:
            try:
                await self.giro()
            except Exception:  # noqa: BLE001 - Sonarr spento o in avvio: si riprova al prossimo giro
                log.warning("Controllo delle puntate col titolo italiano non riuscito", exc_info=True)
            await asyncio.sleep(OGNI)
