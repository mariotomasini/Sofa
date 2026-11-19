"""Configurazione di Beszel (monitoraggio del PC, solo con WITH_BESZEL=si).

Tutto via API: utente, chiave dell'agente, sistema da sorvegliare, soglie di
avviso e notifiche ntfy. L'accesso dal browser lo protegge Caddy, che passa a
Beszel l'utente già autenticato (TRUSTED_AUTH_HEADER).
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx

from ..genera import gateway
from ..impostazioni import Impostazioni
from ..messaggi import ErroreMediabox, info, ok
from ..rete import attendi, controlla

INDIRIZZO = "http://beszel:8090"
FILE_CHIAVE = Path("/volumi/beszel_chiave/beszel.pub")
NOME_SISTEMA = "mediabox"

# nome dell'avviso in Beszel, soglia, minuti prima di avvisare
AVVISI = [
    ("Status", 0, 1),        # il PC non risponde
    ("CPU", 90, 10),         # processore oltre il 90% per 10 minuti
    ("Memory", 90, 10),      # memoria oltre il 90%
    ("Disk", 90, 10),        # disco pieno oltre il 90%
    ("Temperature", 85, 5),  # temperatura oltre 85 °C
]


def email(imp: Impostazioni) -> str:
    return f"{imp.utente}@mediabox.local"


class Beszel:
    def __init__(self) -> None:
        self.client = httpx.Client(base_url=INDIRIZZO, timeout=30)

    def accedi(self, imp: Impostazioni) -> str:
        r = self.client.post(
            "/api/collections/users/auth-with-password",
            json={"identity": email(imp), "password": imp.password},
        )
        if r.status_code in (400, 401):
            raise ErroreMediabox(
                "Beszel non accetta la password di amministrazione.",
                "Se l'hai cambiata da Beszel, rimetti la stessa in ADMIN_PASSWORD nel file .env.",
            )
        dati = controlla(r, "accedere a Beszel").json()
        self.client.headers["Authorization"] = dati["token"]
        return dati["record"]["id"]

    def elenco(self, collezione: str, filtro: str = "") -> list[dict]:
        parametri = {"perPage": 200}
        if filtro:
            parametri["filter"] = filtro
        r = self.client.get(f"/api/collections/{collezione}/records", params=parametri)
        return controlla(r, f"leggere {collezione} in Beszel").json().get("items", [])

    def crea(self, collezione: str, dati: dict) -> dict:
        r = self.client.post(f"/api/collections/{collezione}/records", json=dati)
        return controlla(r, f"salvare {collezione}").json()

    def aggiorna(self, collezione: str, id_record: str, dati: dict) -> dict:
        r = self.client.patch(f"/api/collections/{collezione}/records/{id_record}", json=dati)
        return controlla(r, f"aggiornare {collezione}").json()


def indirizzo_agente(imp: Impostazioni) -> str:
    # Su Linux l'agente usa la rete del PC: il hub lo raggiunge dal "cancello" della rete interna
    return "beszel-agent" if imp.windows else gateway(imp.get("SUBNET", "172.28.64.0/24"))


def configura(imp: Impostazioni, esito) -> None:
    attendi("Beszel", f"{INDIRIZZO}/api/health")
    bz = Beszel()
    if bz.client.get("/api/beszel/first-run").json().get("firstRun"):
        controlla(
            bz.client.post("/api/beszel/create-user", json={"email": email(imp), "password": imp.password}),
            "creare l'utente di Beszel",
        )
        ok("Utente di Beszel creato")
    id_utente = bz.accedi(imp)

    # chiave pubblica del hub → l'agente la legge dal volume condiviso
    chiave = controlla(bz.client.get("/api/beszel/getkey"), "leggere la chiave di Beszel").json()["key"]
    if not FILE_CHIAVE.exists() or FILE_CHIAVE.read_text().strip() != chiave:
        FILE_CHIAVE.parent.mkdir(parents=True, exist_ok=True)
        FILE_CHIAVE.write_text(chiave + "\n")
        info("Chiave consegnata all'agente di Beszel")

    sistemi = bz.elenco("systems", f'name = "{NOME_SISTEMA}"')
    host = indirizzo_agente(imp)
    if sistemi:
        sistema = sistemi[0]
        if sistema.get("host") != host:
            sistema = bz.aggiorna("systems", sistema["id"], {"host": host, "port": "45876"})
    else:
        sistema = bz.crea("systems", {"name": NOME_SISTEMA, "host": host, "port": "45876", "users": [id_utente]})
    ok("PC aggiunto al monitoraggio")

    presenti = {a.get("name") for a in bz.elenco("alerts", f'system = "{sistema["id"]}"')}
    for nome, valore, minuti in AVVISI:
        if nome not in presenti:
            bz.crea("alerts", {"user": id_utente, "system": sistema["id"], "name": nome, "value": valore, "min": minuti})
    ok("Avvisi attivi: PC spento, processore, memoria, disco oltre il 90%, temperatura oltre 85 °C")

    topic = imp.get("NTFY_TOPIC")
    if topic:
        server = imp.get("NTFY_SERVER", "https://ntfy.sh").split("://", 1)[-1].rstrip("/")
        url = f"ntfy://{server}/{topic}"
        impostazioni = bz.elenco("user_settings", f'user = "{id_utente}"')
        if impostazioni:
            attuali = impostazioni[0].get("settings") or {}
            webhook = list(attuali.get("webhooks") or [])
            if url not in webhook:
                attuali["webhooks"] = [*webhook, url]
                bz.aggiorna("user_settings", impostazioni[0]["id"], {"settings": attuali})
        ok(f"Gli avvisi arrivano sul telefono (ntfy: {topic})")
    else:
        esito.nota("Beszel: gli avvisi si vedono nella sua pagina. Per riceverli sul telefono imposta ntfy.")

    # l'agente parte appena trova la chiave: aspettiamo che il hub lo veda
    for _ in range(30):
        stato = bz.elenco("systems", f'name = "{NOME_SISTEMA}"')
        if stato and stato[0].get("status") == "up":
            ok("Beszel riceve i dati del PC")
            return
        time.sleep(3)
    esito.da_fare(
        "Beszel non riceve ancora i dati del PC: esegui \"mediabox restart beszel-agent\" e ricontrolla tra un minuto."
    )
