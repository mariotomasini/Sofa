"""La pagina Download raggruppata: una scheda per ogni film e una per ogni serie.

Mette insieme tre fonti:
- "auto":   quello che Radarr e Sonarr scaricano o cercano (automatici.coda);
- "attesa": già scaricati ma non nella lingua preferita (automatici.in_attesa);
- "cerca":  i download avviati da Cerca (torrent, Internet Archive, puntate cercate con il titolo italiano).

Ogni gruppo ha la copertina, lo stato in breve ("In download · 45%", "2 in download · 5 da trovare"),
l'avanzamento complessivo e l'elenco delle voci con il loro stato, che si vede toccando la copertina.
"""

from __future__ import annotations

import time

# Dal più urgente: decide lo stato della scheda e l'ordine delle schede
ORDINE = ["problema", "download", "importazione", "coda", "da_associare", "cerco", "non_uscito", "attesa", "pronto"]
DA_CERCA = {  # stati dei download di Cerca → stati della pagina
    "in_corso": "download", "completato": "importazione", "importazione": "importazione",
    "importato": "pronto", "da_associare": "da_associare", "errore": "problema",
}
PRONTI_PER = 3 * 24 * 3600  # i download di Cerca già in Jellyfin restano in vista 3 giorni


def _voce_cerca(d: dict) -> dict:
    stato = DA_CERCA.get(d.get("stato", ""), "download")
    return {**d, "origine": "cerca", "stato_pagina": stato, "voce": d.get("titolo", "")}


def _chiave_cerca(d: dict) -> tuple[str, str]:
    if d.get("serie"):
        titolo = (d.get("titolo") or "Serie").split(" · ")[0]
        return f"serie:{d['serie']}", titolo
    if d.get("tmdb_id"):
        anno = f" ({d['anno']})" if d.get("anno") else ""
        return f"tmdb:{d['tmdb_id']}", f"{d.get('titolo', '')}{anno}"
    return f"dl:{d.get('id')}", d.get("titolo") or "Torrent"


def _puntate(v: dict) -> int:
    return len(v.get("ids") or []) or 1


def riassunto(voci: list[dict], lingua: str) -> str:
    """Lo stato della scheda in poche parole."""
    conta: dict[str, int] = {}
    for v in voci:
        conta[v["stato_pagina"]] = conta.get(v["stato_pagina"], 0) + (_puntate(v) if v["stato_pagina"] == "cerco" else 1)
    if conta.get("problema"):
        return "C'è un problema: tocca per vedere"
    parti = []
    if conta.get("download"):
        attivi = [v for v in voci if v["stato_pagina"] == "download"]
        if len(attivi) == 1 and len(voci) == 1:
            return f"In download · {round(attivi[0].get('percentuale') or 0)}%"
        parti.append(f"{conta['download']} in download")
    if conta.get("importazione"):
        n = conta["importazione"]
        parti.append("lo metto in Jellyfin" if n == 1 else f"{n} in Jellyfin a breve")
    if conta.get("coda"):
        parti.append(f"{conta['coda']} in coda")
    if conta.get("da_associare"):
        parti.append("dimmi che film è")
    if conta.get("cerco"):
        parti.append("cerco una fonte" if len(voci) == 1 and conta["cerco"] == 1 else f"{conta['cerco']} da trovare")
    if conta.get("non_uscito"):
        parti.append("non ancora uscito")
    if conta.get("attesa"):
        parti.append(f"aspetto la versione in {lingua.lower()}")
    if not parti and conta.get("pronto"):
        return "Pronto in Jellyfin"
    testo = " · ".join(parti)
    return testo[:1].upper() + testo[1:]


def raggruppa(coda: list[dict], attesa: list[dict], cerca: list[dict], lingua: str = "Italiano",
              adesso: float | None = None) -> list[dict]:
    adesso = adesso or time.time()
    gruppi: dict[str, dict] = {}

    def aggiungi(chiave: str, titolo: str, tipo: str, poster: str, voce: dict) -> None:
        g = gruppi.setdefault(chiave, {"chiave": chiave, "titolo": titolo, "tipo": tipo, "poster": "", "voci": []})
        g["poster"] = g["poster"] or poster
        g["voci"].append(voce)

    for v in coda:
        stato = v.get("stato") or "download"
        aggiungi(v.get("gruppo") or v.get("titolo", ""), v.get("gruppo_titolo") or v.get("titolo", ""),
                 v.get("tipo", "film"), v.get("poster", ""), {**v, "origine": "auto", "stato_pagina": stato})
    for v in attesa:
        aggiungi(v.get("gruppo") or v.get("titolo", ""), v.get("gruppo_titolo") or v.get("titolo", ""),
                 v.get("tipo", "film"), v.get("poster", ""), {**v, "origine": "attesa", "stato_pagina": "attesa"})
    for d in cerca:
        if d.get("stato") == "importato" and adesso - (d.get("aggiornato") or 0) > PRONTI_PER:
            continue
        chiave, titolo = _chiave_cerca(d)
        tipo = "serie" if d.get("serie") else "musica" if d.get("categoria") == "musica" else "film"
        aggiungi(chiave, titolo, tipo, d.get("poster", ""), _voce_cerca(d))

    risultato = []
    for g in gruppi.values():
        # per stato; a parità, nell'ordine della coda (le puntate dalla prima) e poi per nome
        voci = sorted(g["voci"], key=lambda v: (ORDINE.index(v["stato_pagina"]) if v["stato_pagina"] in ORDINE else 99,
                                                v.get("posto") or 9999, v.get("voce") or v.get("titolo") or ""))
        attivi = [v for v in voci if v["stato_pagina"] == "download"]
        stato = voci[0]["stato_pagina"]
        if attivi:
            avanzamento = sum(v.get("percentuale") or 0 for v in attivi) / len(attivi)
        elif all(v["stato_pagina"] == "pronto" for v in voci):
            avanzamento = 100
        else:
            avanzamento = 0
        risultato.append({
            **g, "voci": voci, "stato": stato, "stato_testo": riassunto(voci, lingua),
            "percentuale": round(avanzamento, 1),
            "velocita": sum(v.get("velocita") or 0 for v in attivi),
            "rimanente": max((v.get("rimanente") or 0 for v in attivi), default=0),
            "quante": sum(_puntate(v) for v in voci) if g["tipo"] == "serie" else len(voci),
        })
    return sorted(risultato, key=lambda g: (ORDINE.index(g["stato"]) if g["stato"] in ORDINE else 99, g["titolo"].lower()))
