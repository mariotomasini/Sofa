"""Configurazione di Immich (le foto dei telefoni di casa, solo con WITH_IMMICH=si).

Ogni persona ha il suo account e vede solo le sue foto, i suoi album e i suoi volti:
niente è condiviso finché non lo sceglie lei dall'app. Per questo:

- l'amministratore di Immich è un account a parte (email <ADMIN_USER>@mediabox.local,
  password di mediabox) che non è il profilo di nessuno: gestisce gli utenti e lo
  spazio, ma dall'app non vede le foto degli altri;
- per ogni persona (FOTO_PERSONE, se no UTENTI_CASA) un account con email
  nome@mediabox.local e una password iniziale casuale, da cambiare al primo accesso;
- le foto di ognuno finiscono in una cartella con il suo nome
  (MEDIA_DIR/foto/library/<nome>/<anno>/...), così sul disco e nelle copie è chiaro
  di chi sono.

Immich non sta dietro la password di Caddy: l'app per telefono non saprebbe passarla,
e Immich ha già il suo accesso.
"""

from __future__ import annotations

import re
import secrets
import unicodedata

import httpx

from ..impostazioni import Impostazioni
from ..messaggi import ErroreMediabox, ok
from ..rete import attendi, controlla

INDIRIZZO = "http://immich-server:2283"
DOMINIO = "mediabox.local"
# Cartelle per persona e per data: library/<nome>/2026/2026-11-27/IMG_0001.HEIC
MODELLO_CARTELLE = "{{y}}/{{y}}-{{MM}}-{{dd}}/{{filename}}"
ALFABETO = "abcdefghjkmnpqrstuvwxyz23456789"  # niente 0/o, 1/l/i: si legge e si scrive facile


def nome_breve(nome: str) -> str:
    """"Anna Maria" → "anna.maria": per l'email e per la cartella delle foto."""
    senza_accenti = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    breve = re.sub(r"[^a-z0-9]+", ".", senza_accenti.lower()).strip(".")
    return breve or "utente"


def email(nome: str) -> str:
    return f"{nome_breve(nome)}@{DOMINIO}"


def password_iniziale() -> str:
    """Tre gruppi di quattro caratteri (es. k7fm-q2xa-9pwe): da cambiare al primo accesso."""
    return "-".join("".join(secrets.choice(ALFABETO) for _ in range(4)) for _ in range(3))


def indirizzo_app(imp: Impostazioni) -> str:
    """Per il browser: il nome di mediabox."""
    return f"http://{imp.get('NOME_HOST', 'mediabox.local')}:{imp.get('FOTO_PORT', '2283') or '2283'}"


def indirizzo_telefono(imp: Impostazioni) -> str:
    """Per l'app: meglio l'indirizzo numerico del PC, che il telefono trova sempre
    (come per l'app Jellyfin della TV)."""
    host = imp.get("HOST_IP") or imp.get("NOME_HOST", "mediabox.local")
    return f"http://{host}:{imp.get('FOTO_PORT', '2283') or '2283'}"


def indirizzo_tailscale(imp: Impostazioni) -> str:
    ip = imp.get("TAILSCALE_IP")
    return f"http://{ip}:{imp.get('FOTO_PORT', '2283') or '2283'}" if ip else ""


class Immich:
    def __init__(self) -> None:
        self.client = httpx.Client(base_url=INDIRIZZO + "/api", timeout=30)

    def inizializzato(self) -> bool:
        r = controlla(self.client.get("/server/config"), "leggere lo stato di Immich")
        return bool(r.json().get("isInitialized"))

    def crea_amministratore(self, imp: Impostazioni) -> None:
        dati = {"email": email(imp.utente), "name": "Amministratore", "password": imp.password}
        controlla(self.client.post("/auth/admin-sign-up", json=dati), "creare l'amministratore di Immich")

    def accedi(self, imp: Impostazioni) -> None:
        r = self.client.post("/auth/login", json={"email": email(imp.utente), "password": imp.password})
        if r.status_code in (400, 401):
            raise ErroreMediabox(
                "Immich non accetta l'amministratore con la password di mediabox.",
                f"Se hai cambiato la password da Immich, rimetti quella di ADMIN_PASSWORD all'account "
                f"\"{email(imp.utente)}\" (in Immich: foto del profilo → Impostazioni account → Password).",
            )
        token = controlla(r, "accedere a Immich").json()["accessToken"]
        self.client.headers["Authorization"] = f"Bearer {token}"

    def impostazioni_di_casa(self, imp: Impostazioni) -> bool:
        """Cartelle per persona e per data, video dell'iPhone tenuti come sono, niente
        controllo delle versioni (gli aggiornamenti li porta mediabox), indirizzo giusto
        nei link di condivisione. Restituisce True se ha cambiato qualcosa."""
        config = controlla(self.client.get("/system-config"), "leggere le impostazioni di Immich").json()
        prima = repr(config)
        modello = config.setdefault("storageTemplate", {})
        modello["enabled"] = True
        modello["template"] = MODELLO_CARTELLE
        # L'iPhone registra in HEVC (.MOV): iPhone, Safari e quasi tutti i browser di oggi lo
        # leggono, quindi niente seconda copia convertita (meno spazio, meno lavoro per il PC).
        # Si convertono solo i video in formati che i browser non leggono.
        video = config.setdefault("ffmpeg", {})
        for chiave, aggiunte in (("acceptedVideoCodecs", ["h264", "hevc"]), ("acceptedContainers", ["mov", "mp4"])):
            attuali = list(video.get(chiave) or [])
            video[chiave] = attuali + [a for a in aggiunte if a not in attuali]
        if video.get("transcode") in (None, "all", "optimal", "bitrate"):
            video["transcode"] = "required"
        config.setdefault("newVersionCheck", {})["enabled"] = False
        config.setdefault("server", {})["externalDomain"] = indirizzo_app(imp)
        if repr(config) == prima:
            return False
        controlla(self.client.put("/system-config", json=config), "salvare le impostazioni di Immich")
        return True

    def utenti(self) -> list[dict]:
        return controlla(self.client.get("/admin/users"), "leggere gli account di Immich").json()

    def crea_utente(self, nome: str, password: str) -> None:
        dati = {
            "email": email(nome), "name": nome, "password": password,
            "shouldChangePassword": True, "storageLabel": nome_breve(nome), "isAdmin": False,
        }
        controlla(self.client.post("/admin/users", json=dati), f"creare l'account delle foto di \"{nome}\"")


def configura(imp: Impostazioni, esito) -> None:
    attendi("Immich", f"{INDIRIZZO}/api/server/ping", timeout=900,
            accetta=lambda r: r.status_code == 200)
    im = Immich()
    if not im.inizializzato():
        im.crea_amministratore(imp)
        ok(f"Amministratore di Immich creato ({email(imp.utente)}, password di mediabox)")
    im.accedi(imp)
    if im.impostazioni_di_casa(imp):
        ok("Immich: foto in una cartella per persona, ordinate per data")

    presenti = {(u.get("email") or "").lower() for u in im.utenti()}
    nuovi: list[tuple[str, str]] = []
    for nome in imp.persone_foto:
        if email(nome) == email(imp.utente):
            esito.nota(f"Foto: \"{nome}\" ha lo stesso nome dell'amministratore, che non ha foto sue. "
                       f"Scegli un altro nome in FOTO_PERSONE.")
            continue
        if email(nome) in presenti:
            continue
        password = password_iniziale()
        im.crea_utente(nome, password)
        presenti.add(email(nome))
        nuovi.append((nome, password))
        ok(f"Account delle foto di \"{nome}\" creato ({email(nome)})")

    if not imp.persone_foto:
        esito.da_fare("Foto: scrivi chi ha le foto (Utenti di casa o FOTO_PERSONE) e rilancia "
                      "\"mediabox configura\": ognuno avrà il suo account.")
    server = indirizzo_telefono(imp)
    fuori = indirizzo_tailscale(imp)
    fuori_casa = f" Fuori casa, con Tailscale acceso: {fuori}." if fuori else ""
    for nome, password in nuovi:
        esito.da_fare(
            f"Foto di {nome}: installa l'app Immich sul telefono, come server scrivi {server}, "
            f"poi email {email(nome)} e password {password}. Al primo accesso Immich ne chiede una nuova, "
            f"che solo {nome} conosce."
        )
    if imp.foto_cloud and imp.storagebox:
        esito.da_fare(
            f"Copia delle foto sullo Storage Box {imp.storagebox}: nella pagina di Hetzner attiva \"SSH\", "
            f"poi sul PC scrivi \"mediabox foto-cloud collega\" e la password dello Storage Box (una volta sola)."
        )
    if imp.foto_cloud:
        esito.da_fare(
            f"Copia delle foto nel cloud: scrivi su carta questa password e tienila fuori casa: "
            f"{imp.get('FOTO_CLOUD_PASSWORD')}. Se il PC si rompe, senza di lei la copia non si può aprire. "
            f"La copia parte ogni notte alle 3."
        )
    elif imp.get("FOTO_CLOUD_REPO") or imp.get("FOTO_CLOUD_ID"):
        esito.da_fare("Copia delle foto nel cloud: mancano FOTO_CLOUD_REPO, FOTO_CLOUD_ID o FOTO_CLOUD_CHIAVE "
                      "nel file .env, quindi è spenta.")
    esito.nota(
        f"Foto: app Immich sul telefono, server {server}.{fuori_casa} Ognuno vede solo le sue foto; "
        f"dal browser: {indirizzo_app(imp)}."
    )
