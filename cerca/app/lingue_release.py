"""Le lingue di audio e sottotitoli dal nome di una release.

Stesse regole dei formati che il configuratore mette in Radarr e Sonarr
(configuratore/servizi/formati.py), così Cerca ordina i risultati come loro:
prima la lingua dell'audio secondo LINGUE_AUDIO, poi il MULTI audio, poi i
sottotitoli nella lingua preferita, poi i multi-sub.

"ITA.SUB", "Sub ENG-ITA", "Subs.ENG.SPA.ITA" sono sottotitoli, non audio.
"""

from __future__ import annotations

import re

SUB = r"subs?|subbed|subtitle[sd]?|softsubs?|hardsubs?|sottotitol[a-z]*"
_SEP, _LINGUA = "[ ._-]", "[a-z]{3}[ ._-]"
_PRIMA = "".join(
    rf"(?<!\b{s}{fra}{_LINGUA * n})"
    for s in ("sub", "subs", "hardsub", "softsub") for fra, n in (("", 0), (_SEP, 0), (_SEP, 1), (_SEP, 2))
)
PAROLE = {
    "it": "ita|italian|italiano|itaeng|itaen|iteng",
    "es": "spa|spanish|español|espanol|castellano|latino|esp",
    "fr": "french|truefrench|vff|vfq|vf2|vfi|fre",
    "de": "german|ger|deutsch",
    "pt": "portuguese|por|pt-?br|dublado",
}
ALTRE = "rus|russian|hindi|hin|tamil|telugu|kor|korean|jap|japanese|chi|chinese|tur|turkish|pol|polish"


def _audio(parole: str) -> re.Pattern:
    return re.compile(rf"{_PRIMA}\b({parole})\b(?![ ._-]*({SUB})\b)", re.I)


def _sottotitoli(parole: str) -> re.Pattern:
    return re.compile(rf"\b({SUB})[ ._-]*([a-z]{{3}}[ ._-]+){{0,2}}({parole})\b"
                      rf"|\b({parole})[ ._-]*({SUB})\b|\bsub({parole})\b", re.I)


AUDIO = {c: _audio(p) for c, p in PAROLE.items()}
SOTTOTITOLI = {c: _sottotitoli(p) for c, p in {**PAROLE, "en": "eng|english"}.items()}
STRANIERO = _audio("|".join([*PAROLE.values(), ALTRE]))
MULTI = re.compile(rf"\b(multi|dual)\b(?![ ._-]*({SUB})\b)|\b(multi|dual)audio\b", re.I)
MULTI_SUB = re.compile(r"\b(multi[ ._-]*subs?|multisubs?)\b", re.I)


def multi_ignoto(nome: str) -> bool:
    """ "MULTi" senza lingue scritte: può avere anche la prima lingua."""
    return bool(MULTI.search(nome)) and not STRANIERO.search(nome)


def lingue_audio(nome: str) -> set[str]:
    """Le lingue dell'audio scritte nel nome; inglese se non ne indica nessuna (e non è un MULTi)."""
    trovate = {c for c, r in AUDIO.items() if r.search(nome)}
    return trovate or ({"en"} if not STRANIERO.search(nome) and not MULTI.search(nome) else set())


def lingue_sottotitoli(nome: str) -> set[str]:
    return {c for c, r in SOTTOTITOLI.items() if r.search(nome)}


def valuta(nome: str, lingue: tuple[str, ...] | list[str]) -> tuple[float, bool, float, bool]:
    """Più alto è meglio: (lingua dell'audio, MULTI, lingua dei sottotitoli, multi-sub).
    Un MULTi senza lingue scritte sta tra la prima e la seconda lingua, come in Radarr e Sonarr."""
    def posto(trovate: set[str]) -> float:
        return -min((lingue.index(c) for c in trovate if c in lingue), default=len(lingue))
    audio = -0.5 if multi_ignoto(nome) else posto(lingue_audio(nome))
    return audio, bool(MULTI.search(nome)), posto(lingue_sottotitoli(nome)), bool(MULTI_SUB.search(nome))
