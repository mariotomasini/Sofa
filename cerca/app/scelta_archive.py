"""Scelta della versione migliore di un film su Internet Archive.

Logica pura (nessuna chiamata di rete), così si può testare con le fixture.

Regole, in ordine di importanza:
1. si considerano solo i file video, scartando anteprime e campioni;
2. i derivati a bassa qualità (512kb, 256kb, ...) si usano solo se non c'è altro;
3. si preferisce la risoluzione più alta (altezza; la larghezza solo se manca
   l'altezza);
4. a parità, il file originale batte il derivato creato da Archive;
5. poi mkv/mp4 battono gli altri formati;
6. infine il file più grande (più bitrate).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote

ESTENSIONI_VIDEO = {
    ".mkv", ".mp4", ".m4v", ".avi", ".mpg", ".mpeg", ".mov", ".ogv", ".webm", ".wmv", ".ts", ".m2ts", ".divx",
}
# Formati che Archive usa per immagini/anteprime e non per video veri
FORMATI_NON_VIDEO = {"thumbnail", "animated gif", "jpeg", "png", "item tile", "archive bittorrent"}
PAROLE_DA_SCARTARE = ("sample", "trailer", "preview", "thumb", "_sample")
BASSA_QUALITA = re.compile(r"(?:^|[_\-. ])(512|256|128|64)kb(?:[_\-. ]|$)", re.IGNORECASE)
FORMATI_BASSA_QUALITA = {"512kb mpeg4", "256kb mpeg4", "64kb mpeg4"}


@dataclass(frozen=True)
class FileVideo:
    identificativo: str
    nome: str
    altezza: int
    larghezza: int
    originale: bool
    formato: str
    dimensione: int
    durata: float | None
    bassa_qualita: bool

    @property
    def estensione(self) -> str:
        return estensione(self.nome)

    @property
    def url(self) -> str:
        return f"https://archive.org/download/{quote(self.identificativo)}/{quote(self.nome)}"

    @property
    def url_torrent(self) -> str:
        return (
            f"https://archive.org/download/{quote(self.identificativo)}/"
            f"{quote(self.identificativo)}_archive.torrent"
        )

    def come_dizionario(self) -> dict:
        return {
            "identificativo": self.identificativo,
            "nome": self.nome,
            "altezza": self.altezza,
            "larghezza": self.larghezza,
            "originale": self.originale,
            "formato": self.formato,
            "dimensione": self.dimensione,
            "durata": self.durata,
            "url": self.url,
            "url_torrent": self.url_torrent,
            "pagina": f"https://archive.org/details/{quote(self.identificativo)}",
        }


def estensione(nome: str) -> str:
    punto = nome.rfind(".")
    return nome[punto:].lower() if punto >= 0 else ""


def _intero(valore) -> int:
    try:
        return int(float(str(valore).strip()))
    except (TypeError, ValueError):
        return 0


def durata_in_secondi(valore) -> float | None:
    """Archive scrive la durata come secondi ("5770.13") o come "1:36:10"."""
    if valore in (None, ""):
        return None
    testo = str(valore).strip()
    try:
        if ":" in testo:
            secondi = 0.0
            for parte in testo.split(":"):
                secondi = secondi * 60 + float(parte)
            return secondi
        return float(testo)
    except ValueError:
        return None


def e_video(file: dict) -> bool:
    nome = file.get("name", "")
    formato = str(file.get("format", "")).lower()
    if formato in FORMATI_NON_VIDEO:
        return False
    if estensione(nome) not in ESTENSIONI_VIDEO:
        return False
    nome_basso = nome.lower()
    return not any(p in nome_basso for p in PAROLE_DA_SCARTARE)


def e_bassa_qualita(file: dict) -> bool:
    formato = str(file.get("format", "")).lower()
    return formato in FORMATI_BASSA_QUALITA or bool(BASSA_QUALITA.search(file.get("name", "")))


def file_video(metadata: dict) -> list[FileVideo]:
    """Elenca i file video di un item, con la risoluzione stimata.

    Spesso gli originali non hanno altezza/larghezza nei metadati, mentre i
    derivati sì: in quel caso l'originale eredita la risoluzione del suo
    derivato migliore (un derivato non può essere più definito dell'originale).
    """
    identificativo = metadata.get("metadata", {}).get("identifier", "")
    grezzi = [f for f in metadata.get("files", []) if e_video(f)]

    risoluzione_da_derivati: dict[str, tuple[int, int]] = {}
    for f in grezzi:
        origine = f.get("original")
        if f.get("source") == "derivative" and origine and not e_bassa_qualita(f):
            attuale = risoluzione_da_derivati.get(origine, (0, 0))
            nuova = (_intero(f.get("height")), _intero(f.get("width")))
            risoluzione_da_derivati[origine] = max(attuale, nuova)

    risultato = []
    for f in grezzi:
        altezza, larghezza = _intero(f.get("height")), _intero(f.get("width"))
        if not altezza and not larghezza:
            altezza, larghezza = risoluzione_da_derivati.get(f.get("name", ""), (0, 0))
        risultato.append(
            FileVideo(
                identificativo=identificativo,
                nome=f.get("name", ""),
                altezza=altezza,
                larghezza=larghezza,
                originale=f.get("source") == "original",
                formato=str(f.get("format", "")),
                dimensione=_intero(f.get("size")),
                durata=durata_in_secondi(f.get("length")),
                bassa_qualita=e_bassa_qualita(f),
            )
        )
    return risultato


def _punteggio_estensione(est: str) -> int:
    return {".mkv": 3, ".mp4": 3, ".m4v": 2}.get(est, 1)


def chiave_ordinamento(f: FileVideo) -> tuple:
    """Più alto = migliore."""
    risoluzione = f.altezza if f.altezza else round(f.larghezza * 9 / 16)
    return (
        not f.bassa_qualita,
        risoluzione,
        f.originale,
        _punteggio_estensione(f.estensione),
        f.dimensione,
    )


def migliore_file(metadata: dict, durata_minima: float | None = None) -> FileVideo | None:
    """Il file migliore di un item, oppure None se non ci sono video adatti.

    `durata_minima` (secondi) scarta spezzoni e trailer quando la durata è nota.
    """
    candidati = file_video(metadata)
    if durata_minima:
        candidati = [f for f in candidati if f.durata is None or f.durata >= durata_minima]
    if not candidati:
        return None
    return max(candidati, key=chiave_ordinamento)


def migliore_tra_item(
    elenco: list[dict],
    durata_minima: float | None = None,
    popolarita: dict[str, int] | None = None,
) -> FileVideo | None:
    """Confronta più item dello stesso film e restituisce il file migliore.

    A parità di qualità vince l'item più scaricato (di solito più affidabile).
    """
    popolarita = popolarita or {}
    migliori = [f for f in (migliore_file(m, durata_minima) for m in elenco) if f is not None]
    if not migliori:
        return None
    return max(migliori, key=lambda f: (chiave_ordinamento(f), popolarita.get(f.identificativo, 0)))


def classifica_item(
    elenco: list[dict],
    durata_minima: float | None = None,
) -> list[FileVideo]:
    """Il file migliore di ogni item, dal migliore al peggiore."""
    migliori = [f for f in (migliore_file(m, durata_minima) for m in elenco) if f is not None]
    return sorted(migliori, key=chiave_ordinamento, reverse=True)
