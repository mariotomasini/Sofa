"""Configurazione di TVHeadend (solo Linux, solo con un tuner USB su /dev/dvb).

Fa automaticamente:
- accesso libero dalla rete interna (la password la chiede Caddy);
- rete DVB-T/T2 con l'elenco dei multiplex italiani;
- collegamento dei tuner trovati alla rete e avvio della ricerca canali.

Resta da fare a mano (spiegato nel README): scegliere la regione se l'elenco
nazionale non trova tutto e "mappare" i canali trovati.
"""

from __future__ import annotations

import json

import httpx

from ..impostazioni import Impostazioni
from ..messaggi import ErroreMediabox, info, ok
from ..rete import attendi

INDIRIZZO = "http://tvheadend:9981/tv"
NOME_RETE = "DVB-T Italia"
ELENCO_MUX = "dvb-t/it-All"


class TVHeadend:
    def __init__(self, imp: Impostazioni) -> None:
        self.client = httpx.Client(
            base_url=f"{INDIRIZZO}/api",
            timeout=30,
            auth=httpx.DigestAuth(imp.utente, imp.password),
        )

    def chiama(self, percorso: str, cosa: str, **dati) -> dict:
        corpo = {k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in dati.items()}
        r = self.client.post(f"/{percorso}", data=corpo)
        if r.status_code in (401, 403):
            raise ErroreMediabox(
                "TVHeadend non mi lascia entrare.",
                "Apri http://mediabox.local/tv, completa la procedura guidata creando l'utente "
                "\"admin\" con la password di mediabox, poi riesegui \"mediabox configura\".",
            )
        if not r.is_success:
            raise ErroreMediabox(f"TVHeadend: non sono riuscito a {cosa} (codice {r.status_code}).")
        return r.json() if r.content else {}


def _nodi_hardware(tv: TVHeadend, uuid: str = "root") -> list[dict]:
    nodi = []
    for nodo in tv.chiama("hardware/tree", "leggere i tuner", uuid=uuid) or []:
        nodi.append(nodo)
        if not nodo.get("leaf"):
            nodi.extend(_nodi_hardware(tv, nodo["uuid"]))
    return nodi


def configura(imp: Impostazioni, esito) -> None:
    attendi("TVHeadend", f"{INDIRIZZO}/api/serverinfo", accetta=lambda r: r.status_code in (200, 401))
    tv = TVHeadend(imp)

    # Accesso libero (anche streaming) per le richieste dalla rete interna.
    voci = tv.chiama("access/entry/grid", "leggere gli accessi", limit=200).get("entries", [])
    if not any(v.get("comment") == "mediabox" for v in voci):
        tv.chiama(
            "access/entry/create",
            "creare l'accesso per mediabox",
            conf={
                "enabled": True,
                "username": "*",
                "prefix": imp.get("SUBNET"),
                "comment": "mediabox",
                "streaming": ["basic", "advanced", "htsp"],
                "dvr": ["basic", "htsp", "all", "all_rw", "failed"],
                "webui": True,
                "admin": True,
            },
        )
    ok("Accesso dalla rete interna configurato")

    reti = tv.chiama("mpegts/network/grid", "leggere le reti", limit=50).get("entries", [])
    rete = next((r for r in reti if r.get("networkname") == NOME_RETE), None)
    if rete is None:
        rete = tv.chiama(
            "mpegts/network/create",
            "creare la rete DVB-T",
            **{"class": "dvb_network_dvbt"},
            conf={"networkname": NOME_RETE, "scanfile": ELENCO_MUX, "autodiscovery": 1, "skipinitscan": False},
        )
        ok(f"Rete \"{NOME_RETE}\" creata con l'elenco dei multiplex italiani")
    uuid_rete = rete.get("uuid")

    tuner = [n for n in _nodi_hardware(tv) if "frontend" in n.get("class", "") and "dvbt" in n.get("class", "")]
    if not tuner:
        esito.da_fare("TVHeadend non vede nessun tuner DVB-T: controlla il cavo e guarda il README (sezione Tuner).")
        return
    for nodo in tuner:
        tv.chiama(
            "idnode/save",
            "collegare il tuner",
            node={"uuid": nodo["uuid"], "enabled": True, "networks": [uuid_rete]},
        )
    ok(f"Tuner collegati alla rete: {len(tuner)}. La ricerca dei canali parte da sola.")
    info("La ricerca dei canali richiede qualche minuto.")
    esito.da_fare(
        "TV: quando la ricerca canali è finita apri http://mediabox.local/tv → Configurazione → "
        "DVB Inputs → Servizi → \"Mappa tutti i servizi\" (dettagli nel README)."
    )
