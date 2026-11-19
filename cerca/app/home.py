"""Home in stile Netflix: le righe della pagina iniziale di Film e Serie TV.

Ordine delle righe:
1-2. consigliati per genere (dai generi di quello che guardi in Jellyfin)
3.   Top 10 della settimana
4.   i 10 appena usciti
5.   Continua a guardare (da Jellyfin)

Ogni riga è indipendente: se Jellyfin non risponde spariscono solo i consigli
personali e "Continua a guardare", il resto si vede lo stesso.
"""

from __future__ import annotations

import asyncio
import logging

from .rete import ErroreServizio
from .tmdb import GENERI_INIZIALI

log = logging.getLogger("cerca.home")

RIGHE_CONSIGLIATI = 2
MASSIMO_PER_RIGA = 18
NOMI = {
    "movie": {"top10": "Top 10 film della settimana", "nuovi": "Appena usciti"},
    "tv": {"top10": "Top 10 serie della settimana", "nuovi": "Serie appena uscite"},
}


def scegli_generi(preferiti: list[int], tipo: str, quanti: int = RIGHE_CONSIGLIATI) -> list[tuple[int, bool]]:
    """I generi delle righe "consigliati": prima i tuoi, poi quelli di partenza.

    Restituisce coppie (id_genere, personale).
    """
    scelti: list[tuple[int, bool]] = []
    for g in preferiti:
        if g not in [s[0] for s in scelti]:
            scelti.append((g, True))
    for g in GENERI_INIZIALI[tipo]:
        if g not in [s[0] for s in scelti]:
            scelti.append((g, False))
    return scelti[:quanti]


def filtra(elementi: list[dict], escludi: set[int]) -> list[dict]:
    """Toglie quello che hai già in libreria (o è già in una riga sopra)."""
    return [e for e in elementi if e["id"] not in escludi][:MASSIMO_PER_RIGA]


def titolo_consigliati(nome: str, personale: bool) -> str:
    return f"Perché ti piace: {nome}" if personale else f"Da non perdere: {nome}"


def _errore(exc: BaseException) -> dict:
    if isinstance(exc, ErroreServizio):
        return exc.come_dizionario()
    log.warning("Errore nella home: %s", exc)
    return {"errore": "Una parte della pagina non si è caricata.", "cosa_fare": "Ricarica tra poco."}


async def _senza_errori(coro, predefinito):
    """Jellyfin è un di più: se manca o non risponde, la home si vede lo stesso."""
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001 — qualunque errore di Jellyfin non deve rompere la pagina
        log.info("Jellyfin non disponibile per la home: %s", exc)
        return predefinito


async def righe(tmdb, jellyfin, tipo: str) -> dict:
    """Le righe della home per tipo "movie" (Film) o "tv" (Serie TV)."""
    preferiti_jf, libreria = await asyncio.gather(
        _senza_errori(jellyfin.generi_preferiti(tipo), []),
        _senza_errori(jellyfin.tmdb_in_libreria(tipo), set()),
    )
    try:
        mappa = await tmdb.id_genere(tipo, preferiti_jf) if preferiti_jf else {}
        nomi = {g["id"]: g["nome"] for g in await tmdb.generi(tipo)}
    except ErroreServizio as exc:
        # senza TMDB restano solo i titoli da continuare
        continua = await _senza_errori(jellyfin.continua(tipo), [])
        risposta: dict = {"righe": [], "errore": exc.come_dizionario()}
        if continua:
            risposta["righe"].append({"tipo": "continua", "titolo": "Continua a guardare", "elementi": continua})
        return risposta

    generi = scegli_generi([mappa[n] for n in preferiti_jf if n in mappa], tipo)
    risultati = await asyncio.gather(
        *(tmdb.scopri(tipo, g) for g, _ in generi),
        tmdb.top_settimana(tipo),
        tmdb.appena_usciti(tipo),
        _senza_errori(jellyfin.continua(tipo), []),
        return_exceptions=True,
    )
    consigliati, (top10, nuovi, continua) = risultati[: len(generi)], risultati[len(generi):]

    risposta = {"righe": []}
    gia_mostrati = set(libreria)
    for (genere, personale), elementi in zip(generi, consigliati, strict=True):
        if isinstance(elementi, BaseException):
            risposta.setdefault("errore", _errore(elementi))
            continue
        elementi = filtra(elementi, gia_mostrati)
        gia_mostrati |= {e["id"] for e in elementi}
        nome = nomi.get(genere, "")
        risposta["righe"].append({
            "tipo": "suggeriti", "titolo": titolo_consigliati(nome, personale),
            "genere": genere, "nome_genere": nome, "elementi": elementi,
        })
    for chiave, elementi in (("top10", top10), ("nuovi", nuovi)):
        if isinstance(elementi, BaseException):
            risposta.setdefault("errore", _errore(elementi))
            continue
        risposta["righe"].append({"tipo": chiave, "titolo": NOMI[tipo][chiave], "elementi": elementi})
    if isinstance(continua, list):
        risposta["righe"].append({"tipo": "continua", "titolo": "Continua a guardare", "elementi": continua})
    risposta["righe"] = [r for r in risposta["righe"] if r["elementi"]]
    return risposta
