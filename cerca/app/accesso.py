"""Chi guarda: ogni persona di casa entra in Cerca con il suo utente di Jellyfin.

- Gli utenti di casa (non amministratori) scelgono la loro faccia; se hanno una
  password, la scrivono. "Continua a guardare" e i consigli seguono quell'utente.
- L'amministratore serve per la configurazione: entra con la password e solo lui
  vede il Pannello.

Il cookie è firmato con un segreto che sta nella cartella dei dati di Cerca: nessuno
può fabbricarsene uno da solo. Dura un anno, così sulla TV si sceglie una volta sola.
"""

from __future__ import annotations

import base64
import contextvars
import hashlib
import hmac
import json
import secrets
from pathlib import Path

COOKIE = "mediabox_utente"
DURATA = 365 * 86400
FILE_SEGRETO = "segreto-accesso"

# l'utente della richiesta in corso: lo legge Jellyfin per "Continua a guardare" e i consigli
utente_corrente: contextvars.ContextVar[dict | None] = contextvars.ContextVar("utente_corrente", default=None)


class Accesso:
    def __init__(self, cartella: Path) -> None:
        self.cartella = cartella
        self._segreto: bytes | None = None

    @property
    def segreto(self) -> bytes:
        if self._segreto is None:
            file = self.cartella / FILE_SEGRETO
            try:
                testo = file.read_text(encoding="utf-8").strip()
            except OSError:
                testo = ""
            if len(testo) < 32:
                testo = secrets.token_hex(32)
                self.cartella.mkdir(parents=True, exist_ok=True)
                file.write_text(testo, encoding="utf-8")
                file.chmod(0o600)
            self._segreto = testo.encode()
        return self._segreto

    def _firma(self, dati: bytes) -> str:
        return hmac.new(self.segreto, dati, hashlib.sha256).hexdigest()

    def cookie(self, utente: dict) -> str:
        """Il valore del cookie per questo utente: {"id", "nome", "admin"}."""
        corpo = {"id": str(utente["id"]), "nome": str(utente["nome"]), "admin": bool(utente.get("admin"))}
        # senza "=" in fondo: nei cookie verrebbe messo tra virgolette
        dati = base64.urlsafe_b64encode(json.dumps(corpo, separators=(",", ":")).encode()).rstrip(b"=")
        return f"{dati.decode()}.{self._firma(dati)}"

    def leggi(self, valore: str | None) -> dict | None:
        """L'utente scritto nel cookie, oppure None se manca o è stato manomesso."""
        if not valore or "." not in valore:
            return None
        dati, firma = valore.rsplit(".", 1)
        if not hmac.compare_digest(firma.encode(), self._firma(dati.encode()).encode()):
            return None
        try:
            utente = json.loads(base64.urlsafe_b64decode(dati.encode() + b"=" * (-len(dati) % 4)))
        except ValueError:
            return None
        if not isinstance(utente, dict) or not utente.get("id"):
            return None
        return {"id": str(utente["id"]), "nome": str(utente.get("nome") or ""), "admin": bool(utente.get("admin"))}


def iniziali(nome: str) -> str:
    """Una o due lettere per la faccia dell'utente: "Mario Rossi" → "MR"."""
    parole = [p for p in nome.replace("_", " ").replace(".", " ").split() if p]
    if not parole:
        return "?"
    if len(parole) == 1:
        return parole[0][:1].upper()
    return (parole[0][:1] + parole[1][:1]).upper()


# percorsi che si aprono anche senza aver scelto chi guarda
LIBERI = ("/chi-guarda", "/esci", "/static/", "/salute")


def percorso_libero(percorso: str, base: str = "") -> bool:
    if base and percorso.startswith(base):
        percorso = percorso[len(base):] or "/"
    return any(percorso == p or (p.endswith("/") and percorso.startswith(p)) for p in LIBERI)


def destinazione_sicura(dopo: str, base: str = "") -> str:
    """Dove tornare dopo l'accesso: solo pagine di Cerca, mai indirizzi esterni."""
    if not dopo.startswith("/") or dopo.startswith("//") or "\\" in dopo or percorso_libero(dopo, base):
        return f"{base}/"
    return dopo
