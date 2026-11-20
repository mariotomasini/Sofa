"""Formati personalizzati e profilo qualità "italiano prima di tutto".

Valgono per Radarr e per Sonarr (stessa API v3). L'idea:

- tutte le qualità ammesse (SD → 1080p) stanno in un unico gruppo, così Radarr
  e Sonarr non scelgono per qualità ma per punteggio dei formati;
- l'audio italiano vale molto più di qualsiasi differenza di qualità:
  un 720p ITA batte sempre un 1080p solo inglese;
- se non c'è niente in italiano scarica comunque la versione migliore, e appena
  esce una versione ITA la sostituisce da solo (aggiornamento per punteggio).
"""

from __future__ import annotations

import copy

from .arr import Arr

NOME_PROFILO = "Migliore disponibile"
NOME_GRUPPO = "SD-1080p (decide il punteggio)"
ID_GRUPPO = 1001

# "ITA", "iTALiAN", "Italiano", "ITA-ENG"… ma non "Sub ITA" (solo sottotitoli)
RE_AUDIO_ITA = r"(?i)(?<!\bsub)(?<!\bsub[ ._-])(?<!\bsubs[ ._-])\b(ita|italian|italiano|itaeng|iteng)\b"
RE_SUB_ITA = r"(?i)\b(subs?|subbed|sottotitol[a-z]*)[ ._-]*(ita|italian|italiano)\b"
RE_MULTI = r"(?i)\b(multi|dual)\b"
# Audio preso dal cinema (MD = mic dubbed, LD = line dubbed): ITA sì, ma scarso
RE_AUDIO_CINEMA = r"(?i)\b(md|ld|mic[ ._-]?dubbed|line[ ._-]?dubbed)\b"
RE_3D = r"(?i)\b(3d|sbs|half[ ._-]?ou)\b"

PUNTI_ITA = 10000
PUNTI_1080P = 200

# nome → (punti, condizioni). Ogni condizione: (tipo, valore, negata, obbligatoria)
FORMATI: dict[str, tuple[int, list[tuple[str, object, bool, bool]]]] = {
    "ITA · Audio italiano": (PUNTI_ITA, [("titolo", RE_AUDIO_ITA, False, False)]),
    "ITA · MULTI": (2000, [("titolo", RE_MULTI, False, False)]),
    "ITA · Sottotitoli italiani": (300, [("titolo", RE_SUB_ITA, False, False)]),
    "Qualità · 1080p": (PUNTI_1080P, [("risoluzione", 1080, False, True)]),
    "Qualità · 720p": (100, [("risoluzione", 720, False, True)]),
    "Scarto · Audio dal cinema (MD/LD)": (-5000, [("titolo", RE_AUDIO_CINEMA, False, False)]),
    "Scarto · 3D": (-20000, [("titolo", RE_3D, False, False)]),
}

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


def costruisci_formato(schemi: list[dict], nome: str) -> dict:
    _, condizioni = FORMATI[nome]
    return {
        "name": nome,
        "includeCustomFormatWhenRenaming": False,
        "specifications": [
            condizione(schemi, f"{nome} {i + 1}", tipo, valore, negata, obbligatoria)
            for i, (tipo, valore, negata, obbligatoria) in enumerate(condizioni)
        ],
    }


def assicura_formati(arr: Arr) -> dict[str, int]:
    """Crea o aggiorna i formati di mediabox; restituisce nome → id di tutti i formati."""
    schemi = arr.get("customformat/schema")
    esistenti = {f["name"]: f for f in arr.get("customformat")}
    for nome in FORMATI:
        nuovo = costruisci_formato(schemi, nome)
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


def costruisci_profilo(schema: dict, ammessa, formati: dict[str, int], lingua: dict | None = None) -> dict:
    """Profilo con tutte le qualità ammesse in un solo gruppo e i punteggi ITA."""
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
    profilo["formatItems"] = [
        {"format": i, "name": n, "score": FORMATI[n][0] if n in FORMATI else 0} for n, i in formati.items()
    ]
    profilo.update({
        "name": NOME_PROFILO,
        "upgradeAllowed": True,
        "cutoff": ID_GRUPPO,
        "minFormatScore": 0,  # se non c'è l'italiano scarica comunque
        "cutoffFormatScore": PUNTI_ITA + PUNTI_1080P,  # smette di cercare con ITA 1080p
        "minUpgradeFormatScore": 1,
    })
    if lingua is not None and "language" in profilo:
        profilo["language"] = lingua
    return profilo


def assicura_profilo(arr: Arr, ammessa, lingua: dict | None = None) -> int:
    formati = assicura_formati(arr)
    profilo = costruisci_profilo(arr.get("qualityprofile/schema"), ammessa, formati, lingua)
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
