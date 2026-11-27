"""StreamingCommunity (oggi anche "StreamingUnity") come fonte diretta di film e serie.

È spento finché non metti l'indirizzo in STREAMINGCOMMUNITY_URL: il sito cambia dominio
spesso (e i domini vecchi diventano copie truffa), quindi l'indirizzo lo sceglie chi lo usa.

Come funziona (sito Laravel + Inertia, lettore vixcloud come AnimeUnity):
- la home ha `data-page` con la "version" di Inertia (serve per chiedere le pagine in JSON);
- ricerca: GET /api/search?q=… → {"data": [{id, slug, name, type: "movie"|"tv", …}]};
- serie: GET /titles/{id}-{slug}/season-{n} (con X-Inertia) → props.loadedSeason.episodes;
- lettore: GET /iframe/{id}[?episode_id=…] → <iframe src="https://vixcloud…/embed/…">;
- vixcloud dà `window.masterPlaylist` (HLS) e a volte `window.downloadUrl` (MP4).
  In tutti e due i casi il file lo prende ffmpeg (download.py → _scarica_hls).

Alcuni domini mettono la lingua nell'indirizzo (/it/…): lo capisco dal redirect della home.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
import time
from urllib.parse import urljoin, urlparse

import httpx

from .animeunity import AGENTE_BROWSER, RE_EMBED, _semplice, estrai_download
from .rete import ErroreServizio

RE_DATA_PAGE = re.compile(r'data-page="([^"]+)"')
RE_MASTER = re.compile(r"window\.masterPlaylist\s*=\s*\{(.*?)\}\s*(?:;|\n\s*window\.)", re.S)
RE_PARAM = re.compile(r"""['"]?(\w+)['"]?\s*:\s*['"]([^'"]*)['"]""")
RE_URL_MASTER = re.compile(r"""url\s*:\s*['"]([^'"]+)['"]""")
RE_FHD = re.compile(r"window\.canPlayFHD\s*=\s*true")
RE_ATTR = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')
DURATA_VERSIONE = 30 * 60


# --- funzioni pure (testate) ---------------------------------------------------

def estrai_data_page(pagina: str) -> dict:
    m = RE_DATA_PAGE.search(pagina or "")
    if not m:
        return {}
    try:
        return json.loads(html.unescape(m.group(1)))
    except ValueError:
        return {}


def anno_di(record: dict) -> str:
    for campo in ("release_date", "last_air_date", "first_air_date", "date"):
        valore = str(record.get(campo) or "")
        if re.match(r"\d{4}", valore):
            return valore[:4]
    return ""


def normalizza(record: dict) -> dict:
    """Un titolo di StreamingCommunity con i nomi di Cerca (come le voci degli anime)."""
    poster = next((i.get("filename") for i in record.get("images") or [] if i.get("type") == "poster"), "")
    return {
        "fonte": "streamingcommunity",
        "id": int(record.get("id") or 0),
        "slug": record.get("slug") or "",
        "titolo": html.unescape(record.get("name") or record.get("title") or ""),
        "altri_titoli": [t for t in [record.get("original_name")] if t],
        "anno": anno_di(record),
        "puntate": int(record.get("episodes_count") or 0),
        "tipo": "Movie" if record.get("type") == "movie" else "TV",
        # i titoli sono doppiati in italiano, salvo quelli segnati "sub ita"
        "doppiato": not bool(record.get("sub_ita")),
        "immagine": poster,
        "tmdb": record.get("tmdb_id"),
        "stagioni_proprie": True,  # le stagioni sono le stesse di Sonarr: niente da indovinare
    }


def estrai_iframe(pagina: str) -> str:
    m = RE_EMBED.search(pagina or "")
    return html.unescape(m.group(1)) if m else ""


def playlist_vixcloud(pagina: str) -> str:
    """Dalla pagina del lettore: l'indirizzo HLS completo di token (o "" se non c'è)."""
    m = RE_MASTER.search(pagina or "")
    if not m:
        return ""
    blocco = m.group(1)
    url = RE_URL_MASTER.search(blocco)
    if not url:
        return ""
    parametri = {k: v for k, v in RE_PARAM.findall(blocco) if k not in ("url",) and v}
    indirizzo = url.group(1).replace("\\/", "/")
    extra = "&".join(f"{k}={v}" for k, v in parametri.items())
    if RE_FHD.search(pagina):
        extra += "&h=1"
    if not extra:
        return indirizzo
    return indirizzo + ("&" if "?" in indirizzo else "?") + extra.lstrip("&")


def _attributi(riga: str) -> dict:
    return {k: v.strip('"') for k, v in RE_ATTR.findall(riga.split(":", 1)[1])}


def scegli_flussi(master: str, base: str, lingue: tuple[str, ...] = ("ita", "it")) -> dict:
    """Dal master HLS: il video migliore e l'audio italiano (se è una traccia a parte)."""
    righe = [r.strip() for r in (master or "").splitlines()]
    video, audio = [], []
    for i, riga in enumerate(righe):
        if riga.startswith("#EXT-X-STREAM-INF"):
            a = _attributi(riga)
            uri = next((r for r in righe[i + 1:] if r and not r.startswith("#")), "")
            if uri:
                video.append((int(a.get("BANDWIDTH") or 0), urljoin(base, uri), a.get("AUDIO", "")))
        elif riga.startswith("#EXT-X-MEDIA") and "TYPE=AUDIO" in riga:
            a = _attributi(riga)
            if a.get("URI"):
                audio.append(((a.get("LANGUAGE") or "").lower(), a.get("GROUP-ID", ""), urljoin(base, a["URI"]),
                              a.get("DEFAULT") == "YES"))
    if not video:
        # non è un master: è già la playlist del video
        return {"video": base, "audio": "", "banda": 0}
    banda, url_video, gruppo = max(video)
    del gruppo
    tracce = sorted(audio, key=lambda t: (t[0] not in lingue, not t[3]))
    return {"video": url_video, "audio": tracce[0][2] if tracce else "", "banda": banda}


def durata_playlist(playlist: str) -> float:
    return sum(float(m) for m in re.findall(r"#EXTINF:([\d.]+)", playlist or ""))


def id_puntata(titolo_id: int, episodio_id: int | None = None) -> str:
    """Come lo ricordo nei download: "t123" (film) o "t123e456" (puntata)."""
    return f"t{titolo_id}" + (f"e{episodio_id}" if episodio_id else "")


def leggi_id_puntata(testo) -> tuple[int, int | None]:
    m = re.fullmatch(r"t(\d+)(?:e(\d+))?", str(testo))
    if not m:
        raise ErroreServizio("Puntata di StreamingCommunity non valida.", "Annulla e riprova dalla scheda.")
    return int(m.group(1)), int(m.group(2)) if m.group(2) else None


# --- il sito -------------------------------------------------------------------

class StreamingCommunity:
    nome = "StreamingCommunity"
    solo_anime = False

    def __init__(self, config) -> None:
        self.config = config
        self._client: httpx.AsyncClient | None = None
        self._versione = ""
        self._prefisso = ""
        self._quando = 0.0
        self._slug: dict[int, str] = {}
        self._lock: asyncio.Lock | None = None

    @property
    def attivo(self) -> bool:
        return bool(self.config.streamingcommunity_url)

    @property
    def base(self) -> str:
        return self.config.streamingcommunity_url

    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30, connect=15), follow_redirects=True,
                headers={"User-Agent": AGENTE_BROWSER, "Accept-Language": "it-IT,it;q=0.9"},
            )
        return self._client

    async def chiudi(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    def _errore(self) -> ErroreServizio:
        return ErroreServizio("StreamingCommunity non risponde.",
                              "Cambia spesso indirizzo: metti quello attuale in STREAMINGCOMMUNITY_URL nel .env.")

    async def _get(self, url: str, **kw) -> httpx.Response:
        try:
            r = await self.client().get(url, **kw)
        except httpx.HTTPError as exc:
            raise self._errore() from exc
        if r.status_code in (403, 503):
            raise ErroreServizio("StreamingCommunity blocca la richiesta (protezione anti-bot).",
                                 "Riprova tra qualche minuto.")
        if not r.is_success:
            raise self._errore()
        return r

    async def _prepara(self) -> None:
        """La versione di Inertia e l'eventuale /it nell'indirizzo, dalla home (ogni mezz'ora)."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._versione and time.monotonic() - self._quando < DURATA_VERSIONE:
                return
            r = await self._get(f"{self.base}/")
            percorso = urlparse(str(r.url)).path.strip("/")
            self._prefisso = f"/{percorso.split('/')[0]}" if re.fullmatch(r"[a-z]{2}(/.*)?", percorso) else ""
            self._versione = str(estrai_data_page(r.text).get("version") or "")
            self._quando = time.monotonic()

    async def _url(self, percorso: str) -> str:
        await self._prepara()
        return f"{self.base}{self._prefisso}{percorso}"

    async def _inertia(self, percorso: str) -> dict:
        intestazioni = {"X-Inertia": "true", "X-Requested-With": "XMLHttpRequest", "Accept": "text/html, application/json"}
        if self._versione:
            intestazioni["X-Inertia-Version"] = self._versione
        r = await self._get(await self._url(percorso), headers=intestazioni)
        try:
            return (r.json() if "json" in r.headers.get("content-type", "") else estrai_data_page(r.text)).get("props") or {}
        except ValueError as exc:
            raise self._errore() from exc

    async def cerca(self, titolo: str) -> list[dict]:
        if not self.attivo:
            raise ErroreServizio("StreamingCommunity non è attivo.",
                                 "Metti l'indirizzo del sito in STREAMINGCOMMUNITY_URL nel .env.")
        r = await self._get(await self._url("/api/search"), params={"q": titolo},
                            headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"})
        try:
            dati = r.json()
        except ValueError as exc:
            raise self._errore() from exc
        voci = [normalizza(x) for x in (dati.get("data") if isinstance(dati, dict) else dati) or [] if x.get("id")]
        self._slug.update({v["id"]: v["slug"] for v in voci})
        return voci

    async def _titolo(self, titolo_id: int) -> dict:
        slug = self._slug.get(titolo_id, "")
        props = await self._inertia(f"/titles/{titolo_id}-{slug}" if slug else f"/titles/{titolo_id}")
        return props.get("title") or {}

    async def puntate(self, titolo_id: int) -> list[dict]:
        """Tutte le puntate di tutte le stagioni: [{id, numero, stagione}]."""
        titolo = await self._titolo(titolo_id)
        slug = titolo.get("slug") or self._slug.get(titolo_id, "")
        risultato = []
        for st in titolo.get("seasons") or []:
            n = st.get("number")
            if n is None:
                continue
            props = await self._inertia(f"/titles/{titolo_id}-{slug}/season-{n}")
            for e in (props.get("loadedSeason") or {}).get("episodes") or []:
                if e.get("id"):
                    risultato.append({"id": id_puntata(titolo_id, e["id"]), "numero": str(e.get("number") or ""),
                                      "stagione": int(n)})
        return risultato

    async def link_video(self, puntata: str) -> tuple[str, dict]:
        """Per ffmpeg: "hls:{json}" con video, audio italiano, durata e banda."""
        titolo_id, episodio_id = leggi_id_puntata(puntata)
        parametri = {"episode_id": episodio_id, "next_episode": 1} if episodio_id else {}
        cornice = (await self._get(await self._url(f"/iframe/{titolo_id}"), params=parametri,
                                   headers={"Referer": f"{self.base}/"})).text
        embed = estrai_iframe(cornice)
        if not embed.startswith("http"):
            raise ErroreServizio("StreamingCommunity non dà il lettore di questo titolo.", "Riprova più tardi.")
        intestazioni = {"Referer": embed, "User-Agent": AGENTE_BROWSER}
        pagina = (await self._get(embed, headers={"Referer": f"{self.base}/"})).text
        diretto = estrai_download(pagina)
        if diretto:
            return "hls:" + json.dumps({"video": diretto, "audio": "", "durata": 0, "banda": 0}), intestazioni
        master_url = playlist_vixcloud(pagina)
        if not master_url:
            raise ErroreServizio("Il lettore di StreamingCommunity è cambiato: non trovo il video.",
                                 "Ci pensano i torrent; segnala il problema per aggiornare Cerca.")
        master = (await self._get(master_url, headers=intestazioni)).text
        flussi = scegli_flussi(master, master_url)
        durata = 0.0
        try:
            durata = durata_playlist((await self._get(flussi["video"], headers=intestazioni)).text)
        except ErroreServizio:
            pass
        return "hls:" + json.dumps({**flussi, "durata": durata}), intestazioni

    async def trova_film(self, film: dict) -> dict | None:
        """Il film su StreamingCommunity, solo se è proprio lui (codice TMDB, o titolo e anno uguali)."""
        voci = []
        for t in dict.fromkeys(x for x in (film.get("titolo"), film.get("titolo_originale")) if x):
            try:
                voci += [v for v in await self.cerca(t) if v["tipo"] == "Movie"]
            except ErroreServizio:
                if not voci:
                    raise
        for v in voci:
            if v.get("tmdb") and str(v["tmdb"]) == str(film.get("id")):
                return v
        nomi = {_semplice(film.get("titolo", "")), _semplice(film.get("titolo_originale", ""))} - {""}
        for v in voci:
            if _semplice(v["titolo"]) in nomi and (not v["anno"] or not film.get("anno") or v["anno"] == str(film["anno"])):
                return v
        return None
