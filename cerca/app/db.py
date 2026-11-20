"""Archivio locale di Cerca (SQLite): cronologia, download, avvisami e cache."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS ricerche (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    testo TEXT NOT NULL,
    quando REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS download (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tmdb_id INTEGER,
    titolo TEXT NOT NULL,
    anno TEXT,
    poster TEXT,
    fonte TEXT NOT NULL,          -- archive | prowlarr | manuale
    tipo TEXT NOT NULL,           -- http | torrent
    url TEXT,
    nome_file TEXT,
    percorso TEXT,                -- file o cartella sul disco
    categoria TEXT,
    hash TEXT,
    stato TEXT NOT NULL,          -- in_corso | completato | importazione | importato | da_associare | errore
    scaricati INTEGER DEFAULT 0,
    totale INTEGER DEFAULT 0,
    velocita INTEGER DEFAULT 0,
    messaggio TEXT,
    creato REAL NOT NULL,
    aggiornato REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS avvisami (
    tmdb_id INTEGER PRIMARY KEY,
    titolo TEXT NOT NULL,
    anno TEXT,
    poster TEXT,
    aggiunto REAL NOT NULL,
    gratis_dal REAL,
    provider TEXT,
    notificato INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS cache (
    chiave TEXT PRIMARY KEY,
    valore TEXT NOT NULL,
    scade REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS cache_scade ON cache (scade);
CREATE TABLE IF NOT EXISTS pulizia (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quando REAL NOT NULL,
    cosa TEXT NOT NULL,
    dimensione INTEGER NOT NULL,
    motivo TEXT
);
CREATE TABLE IF NOT EXISTS stato (
    chiave TEXT PRIMARY KEY,
    valore TEXT
);
"""

STATI_ATTIVI = ("in_corso", "completato", "importazione")
# Le risposte scadute restano una settimana come riserva: se un servizio non risponde,
# meglio la risposta di ieri che una pagina di errore.
RISERVA = 7 * 24 * 3600
LIMITE_CACHE_MB = 60  # oltre, si tolgono le risposte che scadono prima


class Archivio:
    def __init__(self, percorso: Path) -> None:
        percorso.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(percorso, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)

    def _esegui(self, sql: str, parametri: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, parametri)

    def _righe(self, sql: str, parametri: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, parametri).fetchall()]

    # --- cache -----------------------------------------------------------------
    def cache_leggi(self, chiave: str, *, scadute: bool = False) -> Any | None:
        """La risposta salvata; con scadute=True anche quella scaduta (la riserva)."""
        righe = self._righe("SELECT valore, scade FROM cache WHERE chiave = ?", (chiave,))
        if not righe or (righe[0]["scade"] < time.time() and not scadute):
            return None
        return json.loads(righe[0]["valore"])

    def cache_scrivi(self, chiave: str, valore: Any, durata: float) -> None:
        self._esegui(
            "INSERT OR REPLACE INTO cache (chiave, valore, scade) VALUES (?, ?, ?)",
            (chiave, json.dumps(valore), time.time() + durata),
        )

    def cache_togli(self, prefisso: str) -> None:
        self._esegui("DELETE FROM cache WHERE chiave LIKE ?", (prefisso + "%",))

    def cache_pulisci(self) -> int:
        """Toglie le risposte scadute da più di una settimana. Restituisce quante."""
        return self._esegui("DELETE FROM cache WHERE scade < ?", (time.time() - RISERVA,)).rowcount

    def cache_dimensione(self) -> int:
        return int(self._righe("SELECT COALESCE(SUM(LENGTH(valore)), 0) AS n FROM cache")[0]["n"])

    def cache_limita(self, mb: float = LIMITE_CACHE_MB) -> int:
        """Se la cache supera il limite, toglie le risposte che scadono prima. Restituisce quante."""
        limite = int(mb * 1024 * 1024)
        da_liberare = self.cache_dimensione() - limite
        if da_liberare <= 0:
            return 0
        via = []
        for r in self._righe("SELECT chiave, LENGTH(valore) AS n FROM cache ORDER BY scade"):
            via.append(r["chiave"])
            da_liberare -= r["n"]
            if da_liberare <= 0:
                break
        with self._lock:
            self._conn.executemany("DELETE FROM cache WHERE chiave = ?", [(c,) for c in via])
        return len(via)

    # --- cronologia ------------------------------------------------------------
    def aggiungi_ricerca(self, testo: str) -> None:
        testo = testo.strip()
        if not testo:
            return
        self._esegui("DELETE FROM ricerche WHERE lower(testo) = lower(?)", (testo,))
        self._esegui("INSERT INTO ricerche (testo, quando) VALUES (?, ?)", (testo, time.time()))
        self._esegui("DELETE FROM ricerche WHERE id NOT IN (SELECT id FROM ricerche ORDER BY quando DESC LIMIT 30)")

    def ultime_ricerche(self, quante: int = 10) -> list[dict]:
        return self._righe("SELECT testo, quando FROM ricerche ORDER BY quando DESC LIMIT ?", (quante,))

    # --- download --------------------------------------------------------------
    def nuovo_download(self, **campi) -> int:
        adesso = time.time()
        campi.setdefault("stato", "in_corso")
        campi.update(creato=adesso, aggiornato=adesso)
        colonne = ", ".join(campi)
        segnaposti = ", ".join("?" for _ in campi)
        cur = self._esegui(f"INSERT INTO download ({colonne}) VALUES ({segnaposti})", tuple(campi.values()))
        return int(cur.lastrowid)

    def aggiorna_download(self, id_download: int, **campi) -> None:
        campi["aggiornato"] = time.time()
        assegnazioni = ", ".join(f"{k} = ?" for k in campi)
        self._esegui(f"UPDATE download SET {assegnazioni} WHERE id = ?", (*campi.values(), id_download))

    def download(self, id_download: int) -> dict | None:
        righe = self._righe("SELECT * FROM download WHERE id = ?", (id_download,))
        return righe[0] if righe else None

    def elenco_download(self, quanti: int = 50) -> list[dict]:
        return self._righe("SELECT * FROM download ORDER BY creato DESC LIMIT ?", (quanti,))

    def download_per_stato(self, *stati: str) -> list[dict]:
        segnaposti = ", ".join("?" for _ in stati)
        return self._righe(f"SELECT * FROM download WHERE stato IN ({segnaposti}) ORDER BY creato", stati)

    def download_per_hash(self, hash_torrent: str) -> dict | None:
        righe = self._righe("SELECT * FROM download WHERE hash = ?", (hash_torrent.lower(),))
        return righe[0] if righe else None

    def elimina_download(self, id_download: int) -> None:
        self._esegui("DELETE FROM download WHERE id = ?", (id_download,))

    # --- avvisami --------------------------------------------------------------
    def avvisami_aggiungi(self, tmdb_id: int, titolo: str, anno: str, poster: str) -> None:
        self._esegui(
            "INSERT OR IGNORE INTO avvisami (tmdb_id, titolo, anno, poster, aggiunto) VALUES (?, ?, ?, ?, ?)",
            (tmdb_id, titolo, anno, poster, time.time()),
        )

    def avvisami_togli(self, tmdb_id: int) -> None:
        self._esegui("DELETE FROM avvisami WHERE tmdb_id = ?", (tmdb_id,))

    def avvisami_presente(self, tmdb_id: int) -> bool:
        return bool(self._righe("SELECT 1 FROM avvisami WHERE tmdb_id = ?", (tmdb_id,)))

    def avvisami_elenco(self) -> list[dict]:
        righe = self._righe("SELECT * FROM avvisami ORDER BY gratis_dal IS NULL, aggiunto DESC")
        for r in righe:
            r["provider"] = json.loads(r["provider"]) if r["provider"] else []
        return righe

    def avvisami_imposta(self, tmdb_id: int, gratis_dal: float | None, provider: list, notificato: bool) -> None:
        self._esegui(
            "UPDATE avvisami SET gratis_dal = ?, provider = ?, notificato = ? WHERE tmdb_id = ?",
            (gratis_dal, json.dumps(provider), int(notificato), tmdb_id),
        )

    # --- pulizia ----------------------------------------------------------------
    def registra_pulizia(self, cosa: str, dimensione: int, motivo: str) -> None:
        self._esegui(
            "INSERT INTO pulizia (quando, cosa, dimensione, motivo) VALUES (?, ?, ?, ?)",
            (time.time(), cosa, dimensione, motivo),
        )

    def pulizie(self, quante: int = 30) -> list[dict]:
        return self._righe("SELECT * FROM pulizia ORDER BY quando DESC LIMIT ?", (quante,))

    # --- stato generico --------------------------------------------------------
    def stato_leggi(self, chiave: str) -> str | None:
        righe = self._righe("SELECT valore FROM stato WHERE chiave = ?", (chiave,))
        return righe[0]["valore"] if righe else None

    def stato_scrivi(self, chiave: str, valore: str) -> None:
        self._esegui("INSERT OR REPLACE INTO stato (chiave, valore) VALUES (?, ?)", (chiave, valore))
