"""Suggerimenti musicali: artisti simili a quelli che ascolti e tendenze.

- Gli artisti che ascolti li legge da Jellyfin (i più ascoltati, o quelli in
  libreria se non hai ancora ascoltato nulla).
- Per ognuno chiede a ListenBrainz (servizio aperto e gratuito, senza account)
  gli artisti simili. Si inviano solo gli identificativi MusicBrainz degli
  artisti, nessun dato personale.
- Le tendenze sono gli artisti più ascoltati della settimana su ListenBrainz.
- "Aggiungi a Lidarr" fa scaricare a Lidarr gli album dell'artista.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
import unicodedata

import httpx

from .config import Config
from .db import Archivio
from .rete import ErroreServizio, Limitatore, non_raggiungibile

LISTENBRAINZ = "https://api.listenbrainz.org/1"
LISTENBRAINZ_LABS = "https://labs.api.listenbrainz.org"
MUSICBRAINZ = "https://musicbrainz.org/ws/2"
ALGORITMO_SIMILI = "session_based_days_7500_session_300_contribution_5_threshold_10_limit_100_filter_True_skip_30"

# MusicBrainz chiede al massimo 1 richiesta al secondo
_limite_mb = Limitatore(per_secondo=1)
_limite_lb = Limitatore(per_secondo=3)
SEI_ORE = 6 * 3600


log = logging.getLogger("cerca.musica")


def normalizza(nome: str) -> str:
    nome = unicodedata.normalize("NFKD", nome or "")
    nome = "".join(c for c in nome if not unicodedata.combining(c)).lower()
    nome = re.sub(r"^the\s+", "", nome)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", nome).split())


def unisci_simili(risposte: dict[str, list[dict]], da_escludere: set[str], quanti: int = 24) -> list[dict]:
    """Mette insieme gli artisti simili a più artisti di partenza.

    Un artista simile a più artisti che ascolti sale in classifica.
    `risposte` = {nome artista di partenza: [simili da ListenBrainz]}.
    """
    punteggi: dict[str, dict] = {}
    for origine, simili in risposte.items():
        massimo = max((s.get("score") or 0 for s in simili), default=1) or 1
        for s in simili:
            mbid = s.get("artist_mbid")
            nome = s.get("name") or ""
            if not mbid or not nome or normalizza(nome) in da_escludere or mbid in da_escludere:
                continue
            voce = punteggi.setdefault(mbid, {"mbid": mbid, "nome": nome, "descrizione": s.get("comment") or "",
                                              "punteggio": 0.0, "simile_a": []})
            voce["punteggio"] += (s.get("score") or 0) / massimo
            if origine not in voce["simile_a"]:
                voce["simile_a"].append(origine)
    ordinati = sorted(punteggi.values(), key=lambda v: (len(v["simile_a"]), v["punteggio"]), reverse=True)
    return ordinati[:quanti]


def interpreta_tendenze(dati: dict, da_escludere: set[str], quanti: int = 24) -> list[dict]:
    artisti = (dati.get("payload") or {}).get("artists") or []
    risultato = []
    for a in artisti:
        mbid, nome = a.get("artist_mbid"), a.get("artist_name") or ""
        if not mbid or not nome:
            continue
        risultato.append({
            "mbid": mbid,
            "nome": nome,
            "ascolti": a.get("listen_count") or 0,
            "in_libreria": normalizza(nome) in da_escludere or mbid in da_escludere,
        })
    return risultato[:quanti]


class Musica:
    def __init__(self, config: Config, archivio: Archivio, client: httpx.AsyncClient, jellyfin) -> None:
        self.config = config
        self.archivio = archivio
        self.client = client
        self.jellyfin = jellyfin

    # --- servizi esterni ----------------------------------------------------------
    async def _get_json(self, url: str, parametri: dict, chiave: str, durata: float, limite: Limitatore):
        in_cache = self.archivio.cache_leggi(chiave)
        if in_cache is not None:
            return in_cache
        await limite.attendi()
        try:
            r = await self.client.get(url, params=parametri, timeout=30)
        except httpx.HTTPError as exc:
            raise ErroreServizio(
                "Non riesco a contattare ListenBrainz/MusicBrainz.",
                "Controlla la connessione a Internet e riprova tra poco.",
            ) from exc
        if r.status_code == 204 or not r.content:
            dati = {}
        elif r.is_success:
            dati = r.json()
        else:
            raise ErroreServizio(f"Il servizio dei suggerimenti ha risposto con un errore ({r.status_code}).",
                                 "Riprova più tardi.")
        self.archivio.cache_scrivi(chiave, dati, durata)
        return dati

    async def mbid_da_nome(self, nome: str) -> str:
        """Se Jellyfin non conosce l'identificativo MusicBrainz, lo cerchiamo per nome."""
        dati = await self._get_json(
            f"{MUSICBRAINZ}/artist",
            {"query": f'artist:"{nome}"', "fmt": "json", "limit": "3"},
            f"mb:artista:{normalizza(nome)}",
            30 * 24 * 3600,
            _limite_mb,
        )
        for a in dati.get("artists", []):
            if int(a.get("score", 0)) >= 95 and normalizza(a.get("name", "")) == normalizza(nome):
                return a["id"]
        return ""

    async def simili(self, mbid: str) -> list[dict]:
        dati = await self._get_json(
            f"{LISTENBRAINZ_LABS}/similar-artists/json",
            {"artist_mbids": mbid, "algorithm": ALGORITMO_SIMILI},
            f"lb:simili:{mbid}",
            7 * 24 * 3600,
            _limite_lb,
        )
        return dati if isinstance(dati, list) else []

    async def tendenze(self) -> list[dict]:
        nostri = await self._artisti_in_libreria()
        dati = await self._get_json(
            f"{LISTENBRAINZ}/stats/sitewide/artists",
            {"count": "40", "range": "week"},
            "lb:tendenze:settimana",
            SEI_ORE,
            _limite_lb,
        )
        return interpreta_tendenze(dati, nostri)

    # --- la tua libreria ---------------------------------------------------------------
    async def _artisti_in_libreria(self) -> set[str]:
        artisti = await self.jellyfin.artisti(limite=2000)
        esclusi = {normalizza(a["nome"]) for a in artisti}
        esclusi |= {a["mbid"] for a in artisti if a.get("mbid")}
        return esclusi

    async def per_te(self) -> dict:
        ascoltati = await self.jellyfin.artisti_piu_ascoltati(limite=8)
        if not ascoltati:
            return {"suggerimenti": [], "partenza": [], "avviso": (
                "Non c'è ancora musica in Jellyfin. Copia i tuoi album in media/musica, oppure "
                "aggiungi qualche artista con Lidarr: i suggerimenti arrivano da quello che ascolti.")}
        da_escludere = await self._artisti_in_libreria()
        for a in ascoltati:
            if not a.get("mbid"):
                try:
                    a["mbid"] = await self.mbid_da_nome(a["nome"])
                except ErroreServizio:
                    a["mbid"] = ""  # questo artista lo saltiamo, gli altri bastano
        validi = [a for a in ascoltati if a.get("mbid")]
        risposte = await asyncio.gather(*(self.simili(a["mbid"]) for a in validi), return_exceptions=True)
        elenco = {a["nome"]: r for a, r in zip(validi, risposte, strict=True) if isinstance(r, list)}
        return {
            "suggerimenti": unisci_simili(elenco, da_escludere),
            "partenza": [a["nome"] for a in validi],
        }

    # --- la home in stile app musicale --------------------------------------------------------
    async def home(self, deezer, oggi: dt.date | None = None) -> dict:
        """Scorciatoie, i tuoi artisti, consigliati, Top 50 Italia, uscite recenti e tendenze.

        Ogni parte è indipendente: se Jellyfin, ListenBrainz o Deezer non rispondono,
        mancano solo le righe che dipendono da loro.
        """
        oggi = oggi or dt.date.today()

        async def mai_errore(coro, predefinito, secondi: float = 20):
            try:
                return await asyncio.wait_for(coro, secondi)
            except Exception as exc:  # noqa: BLE001 - una riga che manca non deve rompere la pagina
                log.info("Musica: una parte della home non è disponibile (%s)", exc)
                return predefinito

        preferiti, ascoltati, consigli, top, dischi, famosi = await asyncio.gather(
            mai_errore(self.jellyfin.brani(limite=500), []),
            mai_errore(self.jellyfin.artisti_piu_ascoltati(limite=12), []),
            mai_errore(self.per_te(), {}),
            mai_errore(deezer.top_italia(), []),
            mai_errore(deezer.album_di_tendenza(), []),
            mai_errore(deezer.artisti_di_tendenza(), []),
        )
        tuoi, per_te = await asyncio.gather(
            mai_errore(deezer.con_foto([a["nome"] for a in ascoltati]), []),
            mai_errore(deezer.con_foto([a["nome"] for a in (consigli.get("suggerimenti") or [])[:16]]), []),
        )
        dal = (oggi - dt.timedelta(days=120)).isoformat()
        uscite = await mai_errore(deezer.uscite_recenti([a["id"] for a in tuoi[:8]], dal), []) if tuoi else []
        righe = [
            {"tipo": "preferiti", "titolo": "Brani preferiti",
             "sottotitolo": "Quelli che hai scaricato: si ascoltano interi",
             "elementi": preferiti[:5], "quanti": len(preferiti)},
            {"tipo": "artisti", "titolo": "I tuoi artisti", "elementi": tuoi},
            {"tipo": "artisti", "titolo": "Consigliati per te", "sottotitolo": "Simili a quelli che ascolti",
             "elementi": per_te},
            {"tipo": "brani", "titolo": "Top 50 Italia", "elementi": top},
            {"tipo": "album", "titolo": "Uscite recenti per te", "elementi": uscite[:20]},
            {"tipo": "album", "titolo": "Album di tendenza", "elementi": dischi},
            {"tipo": "artisti", "titolo": "Artisti di tendenza", "elementi": famosi},
        ]
        return {"scorciatoie": (tuoi or famosi)[:8], "preferiti": len(preferiti),
                "righe": [r for r in righe if r["elementi"]],
                "avviso": consigli.get("avviso", "")}

    async def segui_per_nome(self, nome: str) -> str:
        """Aggiunge a Lidarr un artista conosciuto per nome (da Deezer): Lidarr vuole l'id MusicBrainz."""
        mbid = await self.mbid_da_nome(nome)
        if not mbid:
            raise ErroreServizio(f"Non trovo \"{nome}\" su MusicBrainz, il catalogo che usa Lidarr.",
                                 "Prova ad aggiungerlo da Lidarr cercandolo con un nome diverso.")
        return await self.aggiungi_a_lidarr(mbid)

    # --- Lidarr --------------------------------------------------------------------------
    async def aggiungi_a_lidarr(self, mbid: str) -> str:
        if not self.config.lidarr_url:
            raise ErroreServizio(
                "Lidarr non è attivo.",
                "Attivalo con \"mediabox impostazioni\" (voce Musica con Lidarr) e riprova.",
            )
        intestazioni = {"X-Api-Key": self.config.lidarr_chiave}
        base = f"{self.config.lidarr_url}/api/v1"
        try:
            esistenti = (await self.client.get(f"{base}/artist", headers=intestazioni, timeout=30)).json()
            for a in esistenti:
                if a.get("foreignArtistId") == mbid:
                    return f"{a.get('artistName')} è già in Lidarr."
            trovati = (await self.client.get(f"{base}/artist/lookup", params={"term": f"lidarr:{mbid}"},
                                             headers=intestazioni, timeout=60)).json()
            if not trovati:
                raise ErroreServizio("Lidarr non trova questo artista.", "Riprova più tardi: il catalogo di Lidarr "
                                     "a volte è lento ad aggiornarsi.")
            artista = trovati[0]
            cartelle = (await self.client.get(f"{base}/rootfolder", headers=intestazioni, timeout=30)).json()
            if not cartelle:
                raise ErroreServizio("Lidarr non ha una cartella per la musica.", "Esegui \"mediabox configura\".")
            cartella = cartelle[0]
            artista.update(
                rootFolderPath=cartella["path"],
                qualityProfileId=cartella.get("defaultQualityProfileId") or 1,
                metadataProfileId=cartella.get("defaultMetadataProfileId") or 1,
                monitored=True,
                monitorNewItems="all",
                # Solo l'ultimo album: si parte leggeri, il resto si aggiunge da Lidarr
                addOptions={"monitor": "latest", "searchForMissingAlbums": True},
            )
            r = await self.client.post(f"{base}/artist", json=artista, headers=intestazioni, timeout=60)
        except httpx.HTTPError as exc:
            raise non_raggiungibile("Lidarr", "/lidarr") from exc
        if not r.is_success:
            raise ErroreServizio(f"Lidarr non ha aggiunto l'artista (errore {r.status_code}).", "Riprova da Lidarr.")
        return f"{artista.get('artistName')} aggiunto: Lidarr scarica il suo ultimo album."
