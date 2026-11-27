"""Scoperte per te: ogni settimana una playlist di musica nuova, già scaricata.

Per ogni persona di casa (con le scoperte accese, lo sono di base):
1. gli artisti che ama: preferiti, album più ascoltati e brani delle sue playlist in
   Navidrome (quello che ha importato o scaricato); se non c'è ancora niente, quelli che
   ascolta in Jellyfin;
2. gli artisti simili secondo ListenBrainz (musica.py), tolti quelli già in casa;
3. per ognuno i brani più ascoltati su Deezer, mai proposti prima: 20 brani in tutto,
   al massimo 2 per artista;
4. la playlist "Scoperte · <data>" va all'importazione (importa_musica.py), che li scarica
   pochi alla volta, li mette nella playlist dell'utente e avvisa una volta sola.

Le playlist delle settimane passate restano: si cancellano da Navidrome, i brani piaciuti
restano nella musica di casa.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time
from collections import Counter

from .accesso import utente_corrente
from .musica import normalizza as normalizza_artista
from .musica import unisci_simili
from .navidrome import Navidrome, credenziali
from .rete import ErroreServizio

log = logging.getLogger("cerca.scoperte")

CHIAVE_SPENTE = "scoperte:spente:"
CHIAVE_ULTIMA = "scoperte:ultima:"
CHIAVE_PROPOSTI = "scoperte:proposti:"
OGNI_CONTROLLO = 3600
GIORNI = 7
BRANI = 20
PER_ARTISTA = 2
MAX_ARTISTI_PARTENZA = 8
MAX_PROPOSTI = 2000
MESI = ("gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic")


def nome_playlist(oggi: dt.date) -> str:
    return f"Scoperte · {oggi.day} {MESI[oggi.month - 1]}"


def artisti_preferiti(nomi: list[str], quanti: int = MAX_ARTISTI_PARTENZA) -> list[str]:
    """I più frequenti, senza doppioni scritti in modo diverso."""
    conta: Counter[str] = Counter()
    scritto: dict[str, str] = {}
    for n in nomi:
        k = normalizza_artista(n)
        if k:
            conta[k] += 1
            scritto.setdefault(k, n)
    return [scritto[k] for k, _ in conta.most_common(quanti)]


def scegli_brani(per_artista: list[tuple[str, list[dict]]], proposti: set[int], quanti: int = BRANI,
                 massimo: int = PER_ARTISTA) -> list[dict]:
    """Un giro per artista alla volta: prima il brano più famoso di ognuno, poi il secondo."""
    scelti: list[dict] = []
    presi = set(proposti)
    for _ in range(massimo):
        for _, brani in per_artista:
            t = next((t for t in brani if t.get("id") not in presi), None)
            if t is None:
                continue
            presi.add(t["id"])
            scelti.append({"titolo": t["titolo"], "artista": t["artista"], "album": t.get("album", ""),
                           "id_deezer": t["id"]})
            if len(scelti) >= quanti:
                return scelti
    return scelti


class Scoperte:
    def __init__(self, config, archivio, client, jellyfin, musica, deezer, importa) -> None:
        self.config = config
        self.archivio = archivio
        self.client = client
        self.jellyfin = jellyfin
        self.musica = musica
        self.deezer = deezer
        self.importa = importa

    # --- acceso o spento, per persona ---

    def accese(self, utente: dict | None) -> bool:
        return self.archivio.stato_leggi(CHIAVE_SPENTE + self._chi(utente)) is None

    def imposta(self, utente: dict | None, accese: bool) -> None:
        if accese:
            self.archivio.stato_togli(CHIAVE_SPENTE + self._chi(utente))
        else:
            self.archivio.stato_scrivi(CHIAVE_SPENTE + self._chi(utente), "1")

    @staticmethod
    def _chi(utente: dict | None) -> str:
        return str((utente or {}).get("id") or "admin")

    def stato(self, utente: dict | None) -> dict:
        ultima = float(self.archivio.stato_leggi(CHIAVE_ULTIMA + self._chi(utente)) or 0)
        prossima = ultima + GIORNI * 86400 if ultima else time.time()
        return {"accese": self.accese(utente), "ultima": ultima, "prossima": prossima,
                "attivo": bool(self.config.navidrome_url), "brani": BRANI}

    # --- chi c'è in casa ---

    async def persone(self) -> list[dict]:
        """Gli utenti di Jellyfin, come li vede Cerca: {"id", "nome", "admin"}."""
        utenti = await self.jellyfin._get("/Users", {})
        return [{"id": u["Id"], "nome": u.get("Name", ""), "admin": bool((u.get("Policy") or {}).get("IsAdministrator"))}
                for u in utenti if u.get("Id") and not (u.get("Policy") or {}).get("IsDisabled")]

    # --- la playlist della settimana ---

    async def _partenza(self, utente: dict) -> list[str]:
        nd = Navidrome(self.config, self.client, *credenziali(self.config, utente))
        try:
            nomi = await nd.preferiti_e_frequenti()
        except ErroreServizio:
            nomi = []
        if not nomi:  # niente in Navidrome: quello che ascolta in Jellyfin
            gettone = utente_corrente.set(utente)
            try:
                nomi = [a["nome"] for a in await self.jellyfin.artisti_piu_ascoltati(limite=MAX_ARTISTI_PARTENZA)]
            except ErroreServizio:
                nomi = []
            finally:
                utente_corrente.reset(gettone)
        return artisti_preferiti(nomi)

    async def _simili(self, partenza: list[str]) -> list[str]:
        mbid = {}
        for nome in partenza:
            try:
                if trovato := await self.musica.mbid_da_nome(nome):
                    mbid[nome] = trovato
            except ErroreServizio:
                continue
        risposte = await asyncio.gather(*(self.musica.simili(m) for m in mbid.values()), return_exceptions=True)
        elenco = {n: r for n, r in zip(mbid, risposte, strict=True) if isinstance(r, list)}
        try:
            in_casa = await self.musica._artisti_in_libreria()
        except ErroreServizio:
            in_casa = set()
        in_casa |= {normalizza_artista(n) for n in partenza}
        return [a["nome"] for a in unisci_simili(elenco, in_casa, quanti=BRANI)]

    async def _brani_famosi(self, nome: str) -> tuple[str, list[dict]]:
        artista = await self.deezer.trova_artista(nome)
        if not artista:
            return nome, []
        dati = await self.deezer._get(f"artist/{artista['id']}/top", {"limit": 5}, 7 * 86400)
        brani = []
        for t in dati.get("data") or []:
            brani.append({"id": t.get("id"), "titolo": t.get("title_short") or t.get("title") or "",
                          "artista": (t.get("artist") or {}).get("name", nome), "artista_id": artista["id"],
                          "album": (t.get("album") or {}).get("title", "")})
        return nome, [b for b in brani if b["id"] and b["titolo"]]

    def _proposti(self, utente: dict) -> set[int]:
        try:
            return set(json.loads(self.archivio.stato_leggi(CHIAVE_PROPOSTI + self._chi(utente)) or "[]"))
        except ValueError:
            return set()

    async def crea(self, utente: dict, oggi: dt.date | None = None) -> dict | None:
        """La playlist di questa settimana per questa persona (None se non c'è ancora abbastanza da cui partire)."""
        partenza = await self._partenza(utente)
        if not partenza:
            log.info("Scoperte per %s: ancora nessun artista da cui partire", utente["nome"])
            return None
        simili = await self._simili(partenza)
        risposte = await asyncio.gather(*(self._brani_famosi(n) for n in simili), return_exceptions=True)
        per_artista = [r for r in risposte if isinstance(r, tuple) and r[1]]
        proposti = self._proposti(utente)
        brani = scegli_brani(per_artista, proposti)
        if not brani:
            log.info("Scoperte per %s: nessun brano nuovo da proporre", utente["nome"])
            return None
        nuovi = list(proposti | {b["id_deezer"] for b in brani})[-MAX_PROPOSTI:]
        self.archivio.stato_scrivi(CHIAVE_PROPOSTI + self._chi(utente), json.dumps(nuovi))
        lavoro = self.importa.nuova_da_brani(utente, nome_playlist(oggi or dt.date.today()), brani, origine="scoperte")
        log.info("Scoperte per %s: %s brani (simili a %s)", utente["nome"], len(brani), ", ".join(partenza[:4]))
        return lavoro

    async def giro(self) -> int:
        """Crea le playlist di chi non ne ha una da almeno una settimana. Restituisce quante."""
        if not self.config.navidrome_url:
            return 0
        create = 0
        for utente in await self.persone():
            if not self.accese(utente):
                continue
            ultima = float(self.archivio.stato_leggi(CHIAVE_ULTIMA + self._chi(utente)) or 0)
            if time.time() - ultima < GIORNI * 86400:
                continue
            try:
                lavoro = await self.crea(utente)
            except ErroreServizio as exc:
                log.warning("Scoperte per %s non create: %s", utente["nome"], exc.cosa)
                continue
            # anche senza brani si riprova tra un giorno, non a ogni controllo
            prossima_prova = time.time() if lavoro else time.time() - (GIORNI - 1) * 86400
            self.archivio.stato_scrivi(CHIAVE_ULTIMA + self._chi(utente), str(prossima_prova))
            create += bool(lavoro)
        return create

    async def ciclo(self) -> None:
        while True:
            try:
                await self.giro()
            except Exception:  # noqa: BLE001 - un errore non deve fermare le scoperte per sempre
                log.exception("Errore nelle scoperte della settimana")
            await asyncio.sleep(OGNI_CONTROLLO)
