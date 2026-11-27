"""Configurazione di Navidrome (musica da ascoltare, solo con WITH_NAVIDROME=si).

Navidrome legge la cartella media/musica (la stessa di Jellyfin e Lidarr) e parla
l'API Subsonic: con lui funzionano le app musicali migliori (Amperfy e play:Sub su
iPhone, Symfonium su Android, Feishin sul computer).

Tutto via API: l'amministratore (utente e password di mediabox), un utente per ogni
persona di casa e una prima scansione della musica che c'è già. Non sta dietro la
password di Caddy: le app non saprebbero passarla, e Navidrome ha già il suo accesso.
"""

from __future__ import annotations

import hashlib
import secrets

import httpx

from ..impostazioni import Impostazioni
from ..messaggi import ErroreMediabox, ok
from ..rete import attendi, controlla

INDIRIZZO = "http://navidrome:4533"
BASE = "/navidrome"
CLIENTE = "mediabox"


def password_casa(nome: str) -> str:
    """Gli utenti di casa in Jellyfin non hanno password; le app di Navidrome invece la
    chiedono sempre. Usiamo il nome in minuscolo: facile da ricordare, e mediabox si
    raggiunge solo da casa o da Tailscale. Si può cambiare da Navidrome."""
    return nome.strip().lower()


class Navidrome:
    def __init__(self) -> None:
        self.client = httpx.Client(base_url=INDIRIZZO + BASE, timeout=30)

    def crea_amministratore(self, imp: Impostazioni) -> bool:
        """Solo alla prima accensione. Restituisce True se l'ha creato adesso."""
        r = self.client.post("/auth/createAdmin", json={"username": imp.utente, "password": imp.password})
        if r.status_code == 403:  # c'è già: va bene
            return False
        controlla(r, "creare l'amministratore di Navidrome")
        return True

    def accedi(self, imp: Impostazioni) -> None:
        r = self.client.post("/auth/login", json={"username": imp.utente, "password": imp.password})
        if r.status_code == 401:
            raise ErroreMediabox(
                "Navidrome non accetta l'utente e la password di mediabox.",
                f"Se li hai cambiati da Navidrome, rimetti la password di ADMIN_PASSWORD all'utente "
                f"\"{imp.utente}\" (in Navidrome: Impostazioni → Utenti).",
            )
        token = controlla(r, "accedere a Navidrome").json()["token"]
        self.client.headers["x-nd-authorization"] = f"Bearer {token}"

    def utenti(self) -> list[dict]:
        return controlla(self.client.get("/api/user"), "leggere gli utenti di Navidrome").json()

    def crea_utente(self, nome: str) -> None:
        dati = {"userName": nome, "name": nome, "password": password_casa(nome), "isAdmin": False}
        controlla(self.client.post("/api/user", json=dati), f"creare l'utente \"{nome}\" in Navidrome")

    def scansiona(self, imp: Impostazioni) -> None:
        """Cerca subito la musica nuova (quella già scaricata compare senza aspettare)."""
        sale = secrets.token_hex(6)
        token = hashlib.md5((imp.password + sale).encode()).hexdigest()  # noqa: S324 — lo chiede l'API Subsonic
        parametri = {"u": imp.utente, "t": token, "s": sale, "v": "1.16.1", "c": CLIENTE, "f": "json"}
        controlla(self.client.get("/rest/startScan.view", params=parametri), "avviare la scansione della musica")


def indirizzo_app(imp: Impostazioni) -> str:
    porta = imp.get("HTTP_PORT", "80")
    return f"http://{imp.get('NOME_HOST', 'mediabox.local')}{'' if porta == '80' else ':' + porta}{BASE}"


def configura(imp: Impostazioni, esito) -> None:
    attendi("Navidrome", f"{INDIRIZZO}/ping", timeout=300)
    nd = Navidrome()
    if nd.crea_amministratore(imp):
        ok(f"Amministratore di Navidrome creato (utente \"{imp.utente}\", password di mediabox)")
    nd.accedi(imp)

    presenti = {u.get("userName", "").lower() for u in nd.utenti()}
    for nome in imp.utenti_casa:
        if nome.lower() not in presenti:
            nd.crea_utente(nome)
            ok(f"Utente di casa \"{nome}\" creato (password: \"{password_casa(nome)}\")")

    nd.scansiona(imp)
    ok("Scansione della musica avviata: gli album in media/musica compaiono in pochi minuti")
    esito.nota(
        f"Musica sul telefono: installa Amperfy (iPhone) o Symfonium (Android) e scrivi come server "
        f"{indirizzo_app(imp)}, con il tuo utente e la tua password."
    )
