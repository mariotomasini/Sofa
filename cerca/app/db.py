"""Archivio locale di Cerca (SQLite): cronologia, download, avvisami e cache."""

from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
import threading
import time
from collections.abc import Iterator
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
    serie INTEGER,                -- id della serie in Sonarr (per le puntate)
    puntate TEXT,                 -- id delle puntate di Sonarr, in JSON (download da AnimeUnity)
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

# Indici creati dopo le colonne aggiunte negli aggiornamenti (serie, puntate): sugli archivi
# vecchi la colonna "serie" esiste solo dopo l'ALTER TABLE.
INDICI = """
CREATE INDEX IF NOT EXISTS download_stato ON download (stato, creato);
CREATE INDEX IF NOT EXISTS download_creato ON download (creato);
CREATE INDEX IF NOT EXISTS download_hash ON download (hash);
CREATE INDEX IF NOT EXISTS download_serie ON download (serie);
CREATE INDEX IF NOT EXISTS download_tmdb ON download (tmdb_id);
CREATE INDEX IF NOT EXISTS ricerche_quando ON ricerche (quando);
"""

log = logging.getLogger("cerca.db")

STATI_ATTIVI = ("in_corso", "completato", "importazione")
# Le colonne che nuovo_download e aggiorna_download possono scrivere: i nomi finiscono
# nel testo della query, quindi solo questi (un nome sbagliato è un errore, non un SQL nuovo).
COLONNE_DOWNLOAD = frozenset({
    "tmdb_id", "titolo", "anno", "poster", "fonte", "tipo", "url", "nome_file", "percorso", "categoria",
    "serie", "puntate", "hash", "stato", "scaricati", "totale", "velocita", "messaggio", "creato", "aggiornato",
})
# Le risposte scadute restano una settimana come riserva: se un servizio non risponde,
# meglio la risposta di ieri che una pagina di errore.
RISERVA = 7 * 24 * 3600
LIMITE_CACHE_MB = 60  # oltre, si tolgono le risposte che scadono prima


class Archivio:
    """Un collegamento a SQLite per ogni thread, senza lucchetti nostri.

    Con un solo collegamento condiviso (e un lucchetto) il ciclo principale di Cerca
    doveva aspettare ogni volta che un thread in sottofondo leggeva o scriveva la
    cache: su un PC lento le pagine restavano in caricamento. In modalità WAL invece
    le letture non aspettano mai le scritture, e le scritture (brevi) si mettono in
    fila da sole grazie a busy_timeout.
    """

    def __init__(self, percorso: Path) -> None:
        percorso.parent.mkdir(parents=True, exist_ok=True)
        self._percorso = percorso
        self._locale = threading.local()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        # archivi creati prima delle serie: aggiungiamo la colonna che manca
        colonne = {r["name"] for r in self._righe("PRAGMA table_info(download)")}
        if "serie" not in colonne:
            self._conn.execute("ALTER TABLE download ADD COLUMN serie INTEGER")
        if "puntate" not in colonne:
            self._conn.execute("ALTER TABLE download ADD COLUMN puntate TEXT")
        self._conn.executescript(INDICI)

    @property
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._locale, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._percorso, isolation_level=None, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.execute("PRAGMA synchronous = NORMAL")  # con WAL è sicuro e molto più veloce sui dischi lenti
            self._locale.conn = conn
        return conn

    def _esegui(self, sql: str, parametri: tuple = ()) -> sqlite3.Cursor:
        return self._conn.execute(sql, parametri)

    def _righe(self, sql: str, parametri: tuple = ()) -> list[dict]:
        return [dict(r) for r in self._conn.execute(sql, parametri).fetchall()]

    @contextlib.contextmanager
    def _transazione(self) -> Iterator[sqlite3.Connection]:
        """Più scritture tutte insieme o nessuna (BEGIN IMMEDIATE: niente scritture altrui a metà)."""
        conn = self._conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    @staticmethod
    def _intervallo(prefisso: str) -> tuple[str, str]:
        """Le chiavi che iniziano con il prefisso sono quelle in [prefisso, prefisso + U+10FFFF).

        Con LIKE "_" e "%" nel prefisso facevano da jolly e le maiuscole non contavano
        ("importa_musica:" prendeva anche "IMPORTA-MUSICA:…"); così invece il confronto è
        esatto e usa l'indice della chiave primaria.
        """
        return prefisso, prefisso + "\U0010ffff"

    def integrita(self) -> str:
        """"ok" se il file dell'archivio è sano, altrimenti la descrizione del problema."""
        righe = self._conn.execute("PRAGMA quick_check").fetchall()
        return "; ".join(str(r[0]) for r in righe) or "ok"

    @staticmethod
    def _colonne_valide(campi: dict) -> None:
        sconosciute = set(campi) - COLONNE_DOWNLOAD
        if sconosciute:
            raise ValueError(f"Colonne sconosciute per un download: {', '.join(sorted(sconosciute))}")

    # --- cache -----------------------------------------------------------------
    def cache_leggi(self, chiave: str, *, scadute: bool = False) -> Any | None:
        """La risposta salvata; con scadute=True anche quella scaduta (la riserva)."""
        riga = self._conn.execute("SELECT valore, scade FROM cache WHERE chiave = ?", (chiave,)).fetchone()
        if riga is None or (riga["scade"] < time.time() and not scadute):
            return None
        try:
            return json.loads(riga["valore"])
        except ValueError:
            # una risposta rovinata (disco pieno, spegnimento a metà) vale come non salvata
            log.warning("Risposta in cache illeggibile, la tolgo: %s", chiave)
            self._esegui("DELETE FROM cache WHERE chiave = ?", (chiave,))
            return None

    def cache_scrivi(self, chiave: str, valore: Any, durata: float) -> None:
        self._esegui(
            "INSERT OR REPLACE INTO cache (chiave, valore, scade) VALUES (?, ?, ?)",
            (chiave, json.dumps(valore), time.time() + durata),
        )

    def cache_togli(self, prefisso: str) -> None:
        self._esegui("DELETE FROM cache WHERE chiave >= ? AND chiave < ?", self._intervallo(prefisso))

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
        with self._transazione() as conn:
            conn.executemany("DELETE FROM cache WHERE chiave = ?", [(c,) for c in via])
        return len(via)

    # --- cronologia ------------------------------------------------------------
    def aggiungi_ricerca(self, testo: str) -> None:
        testo = testo.strip()
        if not testo:
            return
        # tutto insieme: due ricerche contemporanee non lasciano doppioni né più di 30 righe
        with self._transazione() as conn:
            conn.execute("DELETE FROM ricerche WHERE lower(testo) = lower(?)", (testo,))
            conn.execute("INSERT INTO ricerche (testo, quando) VALUES (?, ?)", (testo, time.time()))
            conn.execute("DELETE FROM ricerche WHERE id NOT IN (SELECT id FROM ricerche ORDER BY quando DESC LIMIT 30)")

    def ultime_ricerche(self, quante: int = 10) -> list[dict]:
        return self._righe("SELECT testo, quando FROM ricerche ORDER BY quando DESC LIMIT ?", (quante,))

    # --- download --------------------------------------------------------------
    def nuovo_download(self, **campi) -> int:
        adesso = time.time()
        campi.setdefault("stato", "in_corso")
        campi.update(creato=adesso, aggiornato=adesso)
        self._colonne_valide(campi)
        if campi.get("hash"):
            campi["hash"] = str(campi["hash"]).lower()  # download_per_hash cerca in minuscolo
        colonne = ", ".join(campi)
        segnaposti = ", ".join("?" for _ in campi)
        cur = self._esegui(f"INSERT INTO download ({colonne}) VALUES ({segnaposti})", tuple(campi.values()))
        return int(cur.lastrowid)

    def aggiorna_download(self, id_download: int, **campi) -> None:
        campi.setdefault("aggiornato", time.time())
        self._colonne_valide(campi)
        if campi.get("hash"):
            campi["hash"] = str(campi["hash"]).lower()
        assegnazioni = ", ".join(f"{k} = ?" for k in campi)
        self._esegui(f"UPDATE download SET {assegnazioni} WHERE id = ?", (*campi.values(), id_download))

    def download(self, id_download: int) -> dict | None:
        righe = self._righe("SELECT * FROM download WHERE id = ?", (id_download,))
        return righe[0] if righe else None

    def elenco_download(self, quanti: int = 50) -> list[dict]:
        return self._righe("SELECT * FROM download ORDER BY creato DESC LIMIT ?", (quanti,))

    def conta_download(self, *stati: str, tra_gli_ultimi: int = 60) -> int:
        """Quanti tra gli ultimi download sono in questi stati (il numerino in ogni pagina):
        un conteggio, senza leggere e copiare decine di righe intere a ogni pagina."""
        segnaposti = ", ".join("?" for _ in stati)
        return int(self._conn.execute(
            f"SELECT COUNT(*) FROM (SELECT stato FROM download ORDER BY creato DESC LIMIT ?) "
            f"WHERE stato IN ({segnaposti})", (tra_gli_ultimi, *stati)).fetchone()[0])

    def download_per_stato(self, *stati: str) -> list[dict]:
        segnaposti = ", ".join("?" for _ in stati)
        return self._righe(f"SELECT * FROM download WHERE stato IN ({segnaposti}) ORDER BY creato", stati)

    def download_per_hash(self, hash_torrent: str) -> dict | None:
        righe = self._righe("SELECT * FROM download WHERE hash = ?", (hash_torrent.lower(),))
        return righe[0] if righe else None

    def puntate_in_download(self, serie_id: int) -> set[int]:
        """Le puntate di Sonarr che Cerca sta già scaricando (da AnimeUnity) per questa serie."""
        return set(self.download_puntate(serie_id))

    def download_puntate(self, serie_id: int) -> dict[int, dict]:
        """{id puntata di Sonarr: download di Cerca ancora attivo}."""
        segnaposti = ", ".join("?" for _ in STATI_ATTIVI)
        risultato: dict[int, dict] = {}
        for r in self._righe(f"SELECT * FROM download WHERE serie = ? AND puntate IS NOT NULL "
                             f"AND stato IN ({segnaposti}, 'errore')", (serie_id, *STATI_ATTIVI)):
            try:
                ids = json.loads(r["puntate"] or "[]")
            except ValueError:
                continue
            for i in ids:
                risultato[int(i)] = r
        return risultato

    def download_del_film(self, tmdb_id: int) -> list[dict]:
        return self._righe("SELECT * FROM download WHERE tmdb_id = ? AND serie IS NULL", (tmdb_id,))

    def download_della_serie(self, serie_id: int) -> list[dict]:
        return self._righe("SELECT * FROM download WHERE serie = ?", (serie_id,))

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
            try:
                r["provider"] = json.loads(r["provider"]) if r["provider"] else []
            except ValueError:
                r["provider"] = []  # una riga rovinata non deve far sparire tutto l'elenco
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

    def stato_con_prefisso(self, prefisso: str) -> dict[str, str]:
        return {r["chiave"]: r["valore"] for r in self._righe(
            "SELECT chiave, valore FROM stato WHERE chiave >= ? AND chiave < ?", self._intervallo(prefisso))}

    def stato_togli(self, chiave: str) -> None:
        self._esegui("DELETE FROM stato WHERE chiave = ?", (chiave,))

    def stato_scrivi(self, chiave: str, valore: str) -> None:
        self._esegui("INSERT OR REPLACE INTO stato (chiave, valore) VALUES (?, ?)", (chiave, valore))
