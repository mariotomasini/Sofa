"""Formati personalizzati e profilo qualità "prima la lingua che preferisci".

Valgono per Radarr e per Sonarr (stessa API v3). L'idea:

- tutte le qualità ammesse (SD → 1080p) stanno in un unico gruppo, così Radarr
  e Sonarr non scelgono per qualità ma per punteggio dei formati;
- la lingua dell'audio vale molto più di qualsiasi differenza di qualità, nell'ordine
  di LINGUE_AUDIO (predefinito: italiano, poi spagnolo, poi inglese);
- se non c'è la prima lingua scarica comunque la versione migliore, e appena
  esce nella lingua preferita la sostituisce da solo (aggiornamento per punteggio).
"""

from __future__ import annotations

import copy

from ..messaggi import ErroreMediabox
from .arr import Arr

NOME_PROFILO = "Migliore disponibile"
NOME_GRUPPO = "SD-1080p (decide il punteggio)"
ID_GRUPPO = 1001
PREFISSO_LINGUA = "Lingua · "


# Parole che indicano sottotitoli: "ITA.SUB", "Sub.ENG.ITA", "Multi-Subs" non sono audio
SUB = r"subs?|subbed|subtitle[sd]?|softsubs?|hardsubs?|sottotitol[a-z]*"
# Prima della lingua: "sub" e fino a due altre lingue di tre lettere ("Sub ENG-ITA", "Subs.ENG.SPA.ITA").
# Lookbehind a larghezza fissa: valgono uguali in .NET (Radarr, Sonarr) e in Python (test).
_SEP, _LINGUA = "[ ._-]", "[a-z]{3}[ ._-]"
_PRIMA = "".join(
    rf"(?<!\b{s}{fra}{_LINGUA * n})"
    for s in ("sub", "subs", "hardsub", "softsub") for fra, n in (("", 0), (_SEP, 0), (_SEP, 1), (_SEP, 2))
)


def audio(parole: str) -> str:
    """La parola nel nome della release, ma non vicino a "sub" (quelli sono solo sottotitoli)."""
    return rf"(?i){_PRIMA}\b({parole})\b(?![ ._-]*({SUB})\b)"


# codice → (nome, parole nel titolo della release). L'inglese spesso non è scritto:
# vale "inglese" una release senza nessun'altra lingua indicata.
LINGUE: dict[str, tuple[str, str | None]] = {
    "it": ("Italiano", "ita|italian|italiano|itaeng|iteng"),
    "es": ("Spagnolo", "spa|spanish|español|espanol|castellano|latino|esp"),
    "fr": ("Francese", "french|truefrench|vff|vfq|vf2|vfi|fre"),
    "de": ("Tedesco", "german|ger|deutsch"),
    "pt": ("Portoghese", "portuguese|por|pt-?br|dublado"),
    "en": ("Inglese", None),
}
# altre lingue frequenti: se ci sono, la release non è "solo inglese"
ALTRE_LINGUE = "rus|russian|hindi|hin|tamil|telugu|kor|korean|jap|japanese|chi|chinese|tur|turkish|pol|polish|multi|dual"
LINGUE_PREDEFINITE = ["it", "es", "en"]

RE_AUDIO_ITA = audio(LINGUE["it"][1])
ITA = "ita|italian|italiano"
RE_SUB_ITA = (rf"(?i)\b({SUB})[ ._-]*([a-z]{{3}}[ ._-]+){{0,2}}({ITA})\b"
              rf"|\b({ITA})[ ._-]*({SUB})\b|\bsubita\b")
RE_MULTI = rf"(?i)\b(multi|dual)\b(?![ ._-]*({SUB})\b)"
RE_STRANIERO = audio("|".join([p for _, p in LINGUE.values() if p] + [ALTRE_LINGUE]))
# Audio preso dal cinema (MD = mic dubbed, LD = line dubbed): la lingua c'è, ma scarsa
RE_AUDIO_CINEMA = r"(?i)\b(md|ld|mic[ ._-]?dubbed|line[ ._-]?dubbed)\b"
RE_3D = r"(?i)\b(3d|sbs|half[ ._-]?ou)\b"

PUNTI_LINGUA = [10000, 5000, 2500, 1250, 600, 300]
PUNTI_1080P = 200
# nomi usati dalle versioni precedenti di mediabox: si tolgono
OBSOLETI = ("ITA · Audio italiano", "ITA · MULTI", "ITA · Sottotitoli italiani")

Condizione = tuple[str, object, bool, bool]  # (tipo, valore, negata, obbligatoria)


def nome_lingua(codice: str) -> str:
    return PREFISSO_LINGUA + LINGUE[codice][0]


def nomi_lingue(lingue: list[str]) -> str:
    return " → ".join(LINGUE[c][0].lower() for c in lingue)


def condizioni_lingua(codice: str) -> list[Condizione]:
    parole = LINGUE[codice][1]
    if parole is None:  # inglese: nessun'altra lingua nel nome
        return [("titolo", RE_STRANIERO, True, True)]
    return [("titolo", audio(parole), False, False)]


def formati(lingue: list[str]) -> dict[str, tuple[int, list[Condizione]]]:
    """nome → (punti, condizioni). Tutte le lingue esistono; quelle non scelte valgono 0."""
    elenco: dict[str, tuple[int, list[Condizione]]] = {}
    for codice in LINGUE:
        posto = lingue.index(codice) if codice in lingue else None
        punti = PUNTI_LINGUA[min(posto, len(PUNTI_LINGUA) - 1)] if posto is not None else 0
        elenco[nome_lingua(codice)] = (punti, condizioni_lingua(codice))
    elenco.update({
        "Lingua · MULTI": (1000, [("titolo", RE_MULTI, False, False)]),
        "Sottotitoli · Italiano": (300 if "it" in lingue else 0, [("titolo", RE_SUB_ITA, False, False)]),
        "Qualità · 1080p": (PUNTI_1080P, [("risoluzione", 1080, False, True)]),
        "Qualità · 720p": (100, [("risoluzione", 720, False, True)]),
        # -3000: una prima lingua dal cinema batte ancora una seconda lingua pulita
        "Scarto · Audio dal cinema (MD/LD)": (-3000, [("titolo", RE_AUDIO_CINEMA, False, False)]),
        "Scarto · 3D": (-20000, [("titolo", RE_3D, False, False)]),
    })
    return elenco


IMPLEMENTAZIONI = {"titolo": "ReleaseTitleSpecification", "risoluzione": "ResolutionSpecification"}


def condizione(schemi: list[dict], nome: str, tipo: str, valore, negata: bool, obbligatoria: bool) -> dict:
    base = next(s for s in schemi if s.get("implementation") == IMPLEMENTAZIONI[tipo])
    spec = copy.deepcopy(base)
    spec.pop("presets", None)
    spec.update({"name": nome, "negate": negata, "required": obbligatoria})
    for campo in spec.get("fields", []):
        if campo.get("name") == "value":
            campo["value"] = valore
    return spec


def costruisci_formato(schemi: list[dict], nome: str, condizioni: list[Condizione]) -> dict:
    return {
        "name": nome,
        "includeCustomFormatWhenRenaming": False,
        "specifications": [
            condizione(schemi, f"{nome} {i + 1}", tipo, valore, negata, obbligatoria)
            for i, (tipo, valore, negata, obbligatoria) in enumerate(condizioni)
        ],
    }


def assicura_formati(arr: Arr, definiti: dict[str, tuple[int, list[Condizione]]]) -> dict[str, int]:
    """Crea o aggiorna i formati di mediabox; restituisce nome → id di tutti i formati."""
    schemi = arr.get("customformat/schema")
    esistenti = {f["name"]: f for f in arr.get("customformat")}
    for nome in OBSOLETI:
        if nome in esistenti:
            try:
                arr._r("DELETE", f"customformat/{esistenti[nome]['id']}", f"togliere il vecchio formato \"{nome}\"")
                esistenti.pop(nome)
            except ErroreMediabox:
                pass  # resta, ma con punteggio 0 non conta più
    for nome, (_, condizioni) in definiti.items():
        nuovo = costruisci_formato(schemi, nome, condizioni)
        if nome in esistenti:
            nuovo["id"] = esistenti[nome]["id"]
            arr.put(f"customformat/{nuovo['id']}", nuovo, f"aggiornare il formato \"{nome}\"")
        else:
            esistenti[nome] = arr.post("customformat", nuovo, f"creare il formato \"{nome}\"")
    return {f["name"]: f["id"] for f in arr.get("customformat")}


def foglie(items: list[dict]):
    for e in items:
        if e.get("quality"):
            yield e["quality"]
        else:
            yield from (i["quality"] for i in e.get("items", []) if i.get("quality"))


def costruisci_profilo(schema: dict, ammessa, ids: dict[str, int], lingue: list[str] | None = None,
                       lingua: dict | None = None) -> dict:
    """Profilo con tutte le qualità ammesse in un solo gruppo e i punteggi delle lingue."""
    lingue = lingue or LINGUE_PREDEFINITE
    punti = {n: p for n, (p, _) in formati(lingue).items()}
    profilo = copy.deepcopy(schema)
    qualita = list(foglie(profilo["items"]))
    scartate = [{"quality": q, "items": [], "allowed": False} for q in qualita if not ammessa(q)]
    gruppo = {
        "id": ID_GRUPPO,
        "name": NOME_GRUPPO,
        "allowed": True,
        "items": [{"quality": q, "items": [], "allowed": True} for q in qualita if ammessa(q)],
    }
    profilo["items"] = [*scartate, gruppo]  # l'ultimo è il preferito
    profilo["formatItems"] = [{"format": i, "name": n, "score": punti.get(n, 0)} for n, i in ids.items()]
    profilo.update({
        "name": NOME_PROFILO,
        "upgradeAllowed": True,
        "cutoff": ID_GRUPPO,
        "minFormatScore": 0,  # se non c'è la lingua preferita scarica comunque
        "cutoffFormatScore": PUNTI_LINGUA[0] + PUNTI_1080P,  # si ferma con prima lingua + 1080p
        "minUpgradeFormatScore": 1,
    })
    if lingua is not None and "language" in profilo:
        profilo["language"] = lingua
    return profilo


def assicura_profilo(arr: Arr, ammessa, lingue: list[str], lingua: dict | None = None) -> int:
    ids = assicura_formati(arr, formati(lingue))
    profilo = costruisci_profilo(arr.get("qualityprofile/schema"), ammessa, ids, lingue, lingua)
    esistente = next((p for p in arr.get("qualityprofile") if p["name"] == NOME_PROFILO), None)
    if esistente:
        profilo["id"] = esistente["id"]
        arr.put(f"qualityprofile/{esistente['id']}", profilo, "aggiornare il profilo qualità")
        return esistente["id"]
    return arr.post("qualityprofile", profilo, "creare il profilo qualità")["id"]


# --- dimensione dei file ---------------------------------------------------------
# Radarr e Sonarr misurano in MB per minuto. Riferimento: film di 2 ore.
MINUTI_RIFERIMENTO = 120
# quanto pesa ogni risoluzione rispetto al 1080p
FATTORE_RISOLUZIONE = {1080: 1.0, 720: 0.6}
FATTORE_SD = 0.35


def limiti_dimensione(qualita: dict, max_gb: float) -> tuple[float, float] | None:
    """(massimo, preferito) in MB/minuto per una qualità ammessa; None se 4K o non pertinente."""
    risoluzione = qualita.get("resolution", 0)
    if risoluzione > 1080:
        return None
    fattore = FATTORE_RISOLUZIONE.get(risoluzione, FATTORE_SD)
    massimo = round(max_gb * 1024 / MINUTI_RIFERIMENTO * fattore, 1)
    return massimo, round(massimo / 2, 1)  # preferisce file intorno a metà del massimo


def imposta_dimensioni(arr: Arr, max_gb: float) -> None:
    """Niente file enormi: un film 1080p di 2 ore al massimo `max_gb` GB, di solito la metà."""
    definizioni = arr.get("qualitydefinition")
    for d in definizioni:
        limiti = limiti_dimensione(d.get("quality", {}), max_gb)
        if limiti is None:
            continue
        massimo, preferito = limiti
        d["maxSize"] = massimo
        d["preferredSize"] = preferito
        d["minSize"] = min(d.get("minSize") or 0, preferito)
    arr.put("qualitydefinition/update", definizioni, "impostare le dimensioni massime")


def preferisci_usenet(arr: Arr) -> None:
    """A parità di punteggio (stessa lingua e qualità) vince la fonte Usenet/Telegram (tgarr)."""
    for profilo in arr.get("delayprofile"):
        if profilo.get("preferredProtocol") != "usenet":
            profilo["preferredProtocol"] = "usenet"
            arr.put(f"delayprofile/{profilo['id']}", profilo, "preferire Telegram (tgarr)")
