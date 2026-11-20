"""Aggiornamenti di mediabox da GitHub: controlla, scarica e installa solo il necessario.

Da dove arriva la versione nuova:
- se su GitHub c'è una Release, l'ultima Release (versioni come v1.4.0);
- altrimenti l'ultimo cambiamento del ramo principale (main), riconosciuto dal commit.

Cosa resta com'è: il file .env (tranne le versioni dei programmi non cambiate a
mano), i file generati, i backup, i registri e i file di stato.

Dopo aver copiato i file nuovi scriviamo generato/aggiornamento.json con quello
che serve fare (il "piano"), così lo script sul PC rifà solo quello:
- "cerca": ricostruire Cerca (solo se è cambiato il suo codice)
- "configuratore": ricostruire il configuratore e le impostazioni
- "configura": riconfigurare i servizi via API (solo se è cambiato il configuratore)
Le immagini dei programmi si scaricano solo se mancano; "docker compose up"
ricrea solo i servizi cambiati.

I file si sostituiscono con un nome nuovo e poi uno spostamento: lo script che
sta girando (bash legge il file mentre lo esegue) continua con la versione vecchia.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path

import httpx

REPO = "mariotomasini/Sofa"
RAMO = "main"
INSTALLATA = ".versione-installata"
STATO = "aggiornamento.stato"
PIANO = "generato/aggiornamento.json"
# Quello che appartiene a questo PC e non si tocca mai
DA_LASCIARE = {
    ".git", ".env", "generato", "backup", "dischi.json", "installazione.log", "installazione.stato",
    ".configurazione-pronta", ".da-applicare", ".da-aggiornare", "aggiornamenti.log", STATO, INSTALLATA,
}
SALTA = {"__pycache__", ".pytest_cache", ".ruff_cache", "node_modules"}
COMPONENTI = {"cerca": "cerca", "configuratore": "configuratore"}


class ErroreAggiornamento(Exception):
    pass


def _api(cartella: Path | None = None) -> str:
    """L'indirizzo dell'API di GitHub (un altro solo per le prove: MEDIABOX_GITHUB_API nel .env)."""
    from .impostazioni import leggi_env

    da_env = leggi_env(cartella / ".env").get("MEDIABOX_GITHUB_API", "") if cartella else ""
    return (os.environ.get("MEDIABOX_GITHUB_API") or da_env or "https://api.github.com").rstrip("/")


def _repo(cartella: Path) -> str:
    from .impostazioni import leggi_env

    return (leggi_env(cartella / ".env").get("MEDIABOX_REPO") or REPO).strip()


def _client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(60, connect=15), follow_redirects=True,
                        headers={"Accept": "application/vnd.github+json", "User-Agent": "mediabox-aggiornamenti"})


# --- versioni ---------------------------------------------------------------------------------------

def versione_file(cartella: Path) -> str:
    try:
        return (cartella / "VERSION").read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def installata(cartella: Path) -> dict:
    """La versione sul PC. "id" è il tag o il commit installato ("" se mediabox è stato scaricato a mano)."""
    try:
        dati = json.loads((cartella / INSTALLATA).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        dati = {}
    return {"versione": versione_file(cartella), "fonte": dati.get("fonte", ""), "id": dati.get("id", ""),
            "messaggio": dati.get("messaggio", ""), "data": dati.get("data", ""), "quando": dati.get("quando", "")}


def _numeri(versione: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", versione)[:3]) or (0,)


def piu_nuova(nuova: str, attuale: str) -> bool:
    return _numeri(nuova) > _numeri(attuale)


def ultima(cartella: Path, client: httpx.Client | None = None) -> dict:
    """L'ultima versione su GitHub. Solleva ErroreAggiornamento se GitHub non risponde."""
    repo = _repo(cartella)
    api = _api(cartella)
    proprio = client is None
    client = client or _client()
    try:
        r = client.get(f"{api}/repos/{repo}/releases/latest")
        if r.status_code == 200:
            d = r.json()
            return {"fonte": "release", "id": d.get("tag_name", ""), "versione": d.get("tag_name", "").lstrip("v"),
                    "messaggio": d.get("name") or d.get("tag_name", ""), "data": (d.get("published_at") or "")[:10],
                    "archivio": d.get("zipball_url") or f"{api}/repos/{repo}/zipball/{d.get('tag_name')}"}
        if r.status_code not in (404,):
            raise ErroreAggiornamento(f"GitHub ha risposto con un errore ({r.status_code}).")
        r = client.get(f"{api}/repos/{repo}/commits/{RAMO}")
        if r.status_code != 200:
            raise ErroreAggiornamento(f"GitHub ha risposto con un errore ({r.status_code}).")
        d = r.json()
        commit = d.get("commit") or {}
        return {"fonte": "main", "id": d.get("sha", ""), "versione": "",
                "messaggio": (commit.get("message") or "").splitlines()[0] if commit.get("message") else "",
                "data": ((commit.get("committer") or {}).get("date") or "")[:10],
                "archivio": f"{api}/repos/{repo}/zipball/{d.get('sha')}"}
    except httpx.HTTPError as exc:
        raise ErroreAggiornamento("Non riesco a raggiungere GitHub: controlla la connessione a Internet.") from exc
    finally:
        if proprio:
            client.close()


def c_e_di_nuovo(attuale: dict, nuova: dict) -> bool:
    """Con le Release conta il numero di versione; senza, basta un commit diverso da quello installato."""
    if nuova["fonte"] == "release":
        return piu_nuova(nuova["versione"], attuale["versione"])
    return attuale.get("id") != nuova["id"]


def controlla(cartella: Path, client: httpx.Client | None = None) -> dict:
    """Per la pagina delle impostazioni e per il controllo di ogni notte."""
    attuale = installata(cartella)
    try:
        nuova = ultima(cartella, client)
    except ErroreAggiornamento as exc:
        return {"attuale": attuale, "errore": str(exc), "disponibile": False}
    nuova.pop("archivio", None)
    return {"attuale": attuale, "ultima": nuova, "disponibile": c_e_di_nuovo(attuale, nuova)}


# --- stato per la pagina ------------------------------------------------------------------------------

def scrivi_stato(cartella: Path, fase: str, messaggio: str = "") -> None:
    """fase: controllo | scarico | installo | fatto | aggiornato | errore | in_attesa"""
    dati = {"fase": fase, "messaggio": messaggio, "quando": dt.datetime.now().isoformat(timespec="seconds")}
    _scrivi_sicuro(cartella / STATO, json.dumps(dati, ensure_ascii=False).encode())


def leggi_stato(cartella: Path) -> dict:
    try:
        return json.loads((cartella / STATO).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"fase": "", "messaggio": "", "quando": ""}


# --- installazione ------------------------------------------------------------------------------------

def impronta(cartella: Path) -> str:
    """Un'impronta di tutti i file di una cartella: cambia solo se cambia qualcosa."""
    h = hashlib.sha256()
    if not cartella.exists():
        return ""
    for f in sorted(cartella.rglob("*")):
        if not f.is_file() or SALTA & set(f.relative_to(cartella).parts) or f.suffix == ".pyc":
            continue
        h.update(str(f.relative_to(cartella)).replace(os.sep, "/").encode())
        h.update(f.read_bytes())
    return h.hexdigest()


def _scrivi_sicuro(destinazione: Path, contenuto: bytes, modo: int = 0o644) -> None:
    destinazione.parent.mkdir(parents=True, exist_ok=True)
    fd, temporaneo = tempfile.mkstemp(dir=destinazione.parent, prefix=".nuovo-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(contenuto)
        # mkstemp crea il file leggibile solo da chi lo scrive: la pagina delle impostazioni deve leggerlo
        os.chmod(temporaneo, modo or 0o644)
        os.replace(temporaneo, destinazione)
    except BaseException:
        Path(temporaneo).unlink(missing_ok=True)
        raise


def _da_lasciare(relativo: Path) -> bool:
    return relativo.parts[0] in DA_LASCIARE or bool(SALTA & set(relativo.parts))


def copia(sorgente: Path, cartella: Path) -> int:
    """Copia i file nuovi o cambiati (non quelli uguali). Restituisce quanti ne ha cambiati."""
    cambiati = 0
    for f in sorted(sorgente.rglob("*")):
        relativo = f.relative_to(sorgente)
        if not f.is_file() or _da_lasciare(relativo):
            continue
        destinazione = cartella / relativo
        nuovo = f.read_bytes()
        try:
            if destinazione.read_bytes() == nuovo:
                continue
        except OSError:
            pass
        eseguibile = os.access(f, os.X_OK) or relativo.name in ("mediabox", "install.sh") or relativo.suffix == ".sh"
        _scrivi_sicuro(destinazione, nuovo, 0o755 if eseguibile else 0o644)
        cambiati += 1
    return cambiati


def aggiorna_versioni_immagini(cartella: Path, vecchio_esempio: dict[str, str]) -> list[str]:
    """Le versioni dei programmi (IMG_*) seguono quelle nuove, salvo quelle cambiate a mano nel .env."""
    from .impostazioni import leggi_env
    from .menu import aggiorna_env

    nuovo_esempio = leggi_env(cartella / ".env.example")
    attuali = leggi_env(cartella / ".env")
    modifiche = {}
    for chiave, valore in nuovo_esempio.items():
        if not chiave.startswith("IMG_"):
            continue
        attuale = attuali.get(chiave, "")
        prima = attuale or vecchio_esempio.get(chiave, "")  # senza riga nel .env vale quella dell'esempio
        if (not attuale or attuale == vecchio_esempio.get(chiave)) and prima != valore:
            modifiche[chiave] = valore
    if modifiche and (cartella / ".env").exists():
        aggiorna_env(cartella / ".env", modifiche)
    return sorted(modifiche)


def _estrai(archivio: bytes, dove: Path) -> Path:
    with zipfile.ZipFile(io.BytesIO(archivio)) as z:
        for voce in z.infolist():
            nome = Path(voce.filename)
            if nome.is_absolute() or ".." in nome.parts:
                raise ErroreAggiornamento("L'archivio scaricato non è valido.")
            z.extract(voce, dove)
            modo = (voce.external_attr >> 16) & 0o777
            if modo and not voce.is_dir():
                os.chmod(dove / voce.filename, modo)
    radici = [p for p in dove.iterdir() if p.is_dir()]
    if len(radici) != 1 or not (radici[0] / "VERSION").exists() or not (radici[0] / "mediabox").exists():
        raise ErroreAggiornamento("L'archivio scaricato non sembra mediabox (controlla MEDIABOX_REPO nel file .env).")
    return radici[0]


def installa(cartella: Path, client: httpx.Client | None = None) -> dict | None:
    """Scarica e installa la versione nuova. Restituisce il piano, o None se non c'è niente di nuovo."""
    from .impostazioni import leggi_env

    proprio = client is None
    client = client or _client()
    try:
        scrivi_stato(cartella, "controllo", "Controllo se c'è una versione nuova…")
        attuale = installata(cartella)
        nuova = ultima(cartella, client)
        if not c_e_di_nuovo(attuale, nuova):
            scrivi_stato(cartella, "aggiornato", "mediabox è già all'ultima versione.")
            return None
        scrivi_stato(cartella, "scarico", "Scarico la versione nuova…")
        try:
            r = client.get(nuova["archivio"], headers={"Accept": "*/*"})
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise ErroreAggiornamento("Non riesco a scaricare la versione nuova: riprova più tardi.") from exc
        scrivi_stato(cartella, "installo", "Installo i file cambiati…")
        with tempfile.TemporaryDirectory(prefix="mediabox-aggiornamento-") as temporanea:
            sorgente = _estrai(r.content, Path(temporanea))
            prima = {nome: impronta(cartella / dir_) for nome, dir_ in COMPONENTI.items()}
            vecchio_esempio = leggi_env(cartella / ".env.example")
            cambiati = copia(sorgente, cartella)
        immagini = aggiorna_versioni_immagini(cartella, vecchio_esempio)
        dopo = {nome: impronta(cartella / dir_) for nome, dir_ in COMPONENTI.items()}
        piano = {
            "cerca": prima["cerca"] != dopo["cerca"],
            "configuratore": prima["configuratore"] != dopo["configuratore"],
            "configura": prima["configuratore"] != dopo["configuratore"],
            "immagini": immagini,
            "file_cambiati": cambiati,
            "versione": versione_file(cartella),
            "id": nuova["id"],
        }
        _scrivi_sicuro(cartella / PIANO, json.dumps(piano, indent=2).encode())
        _scrivi_sicuro(cartella / INSTALLATA, json.dumps({
            "fonte": nuova["fonte"], "id": nuova["id"], "messaggio": nuova["messaggio"], "data": nuova["data"],
            "quando": dt.datetime.now().isoformat(timespec="seconds"),
        }, ensure_ascii=False, indent=2).encode())
        return piano
    except ErroreAggiornamento as exc:
        scrivi_stato(cartella, "errore", str(exc))
        raise
    finally:
        if proprio:
            client.close()


def pulisci_temporanei(cartella: Path) -> None:
    """File ".nuovo-*" rimasti da un aggiornamento interrotto."""
    for f in cartella.rglob(".nuovo-*"):
        if not _da_lasciare(f.relative_to(cartella)):
            f.unlink(missing_ok=True)


def main(cartella: Path, azione: str) -> int:
    """python -m configuratore aggiornamento controlla|installa

    Codici di uscita di "installa": 0 installato (piano in generato/aggiornamento.json),
    3 niente di nuovo, 1 errore. "controlla": 0 c'è una versione nuova, 3 no, 1 errore.
    """
    if azione == "controlla":
        esito = controlla(cartella)
        print(json.dumps(esito, ensure_ascii=False, indent=2))
        if esito.get("errore"):
            return 1
        return 0 if esito["disponibile"] else 3
    if azione == "installa":
        try:
            piano = installa(cartella)
        except ErroreAggiornamento as exc:
            print(f"  × {exc}")
            return 1
        if piano is None:
            print("  √ mediabox è già all'ultima versione")
            return 3
        print(f"  √ Installati {piano['file_cambiati']} file cambiati (versione {piano['versione']})")
        cosa = [nome for nome, si in (("Cerca", piano["cerca"]), ("configuratore", piano["configuratore"])) if si]
        print("  · Da ricostruire: " + (", ".join(cosa) if cosa else "niente"))
        if piano["immagini"]:
            nomi = ", ".join(i.removeprefix("IMG_").lower() for i in piano["immagini"])
            print(f"  · Programmi con versione nuova: {nomi}")
        return 0
    print("Azioni: controlla, installa")
    return 2

