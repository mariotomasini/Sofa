"""AnimeWorld come fonte diretta degli anime (come AnimeUnity, vedi animeunity.py).

- ricerca: GET /search?keyword=… (pagina HTML, una voce per `div.item`);
- anno: dal riquadro /api/tooltip/{n} della voce (serve a mettere in ordine le stagioni);
- puntate: tutte nella pagina /play/{voce}, server "AnimeWorld Server" (data-name="9"),
  che è quello con il download diretto;
- file: GET /api/episode/info?id={puntata} → "grabber", l'MP4 vero (non scade, riprende i download).
"""

from __future__ import annotations

import asyncio
import html
import re

import httpx

from .animeunity import AGENTE_BROWSER
from .rete import ErroreServizio

SERVER_DIRETTO = "9"  # "AnimeWorld Server"
TIPI_A_PARTE = {"movie": "Movie", "special": "Special", "ova": "OVA", "ona": "ONA"}

RE_NOME = re.compile(r'<a href="/play/([^"]+)"\s+data-jtitle="([^"]*)"\s+class="name">([^<]*)</a>')
RE_IMMAGINE = re.compile(r'<img[^>]+src="([^"]+)"')
RE_TOOLTIP = re.compile(r'data-tip="api/tooltip/(\d+)"')
RE_TIPO = re.compile(r'<div class="(movie|special|ova|ona)">')
RE_ANNO = re.compile(r"Data di uscita:</label>\s*<span>[^<]*?(\d{4})", re.S | re.I)
RE_PUNTATA = re.compile(r'<a[^>]*\bdata-id="([^"]+)"[^>]*\bdata-num="([^"]*)"')
RE_EPISODI = re.compile(r"<dt>Episodi:</dt>\s*<dd>\s*(\d+)")


# --- funzioni pure (testate) ---------------------------------------------------

def leggi_ricerca(pagina: str) -> list[dict]:
    voci = []
    elenco = (pagina or "").split('<div class="film-list">', 1)[-1]
    elenco = elenco.split('<div id="sidebar"', 1)[0].split("window.paginationUrl", 1)[0]
    for blocco in elenco.split('<div class="item">')[1:]:
        nome = RE_NOME.search(blocco)
        if not nome:
            continue
        percorso, titolo_jp, titolo = nome.groups()
        titolo = html.unescape(titolo).strip()
        tipo = RE_TIPO.search(blocco)
        immagine = RE_IMMAGINE.search(blocco)
        tooltip = RE_TOOLTIP.search(blocco)
        voci.append({
            "fonte": "animeworld", "id": percorso.split("/")[0], "slug": percorso.split("/")[0],
            "titolo": titolo, "altri_titoli": [t for t in [html.unescape(titolo_jp).strip()] if t and t != titolo],
            "anno": "", "puntate": 0, "tipo": TIPI_A_PARTE.get(tipo.group(1), "TV") if tipo else "TV",
            "doppiato": 'class="dub"' in blocco or titolo.endswith("(ITA)"),
            "immagine": html.unescape(immagine.group(1)) if immagine else "",
            "tooltip": tooltip.group(1) if tooltip else "",
        })
    return voci


def leggi_puntate(pagina: str) -> list[dict]:
    """Le puntate del server con il download diretto: [{id, numero (testo)}]."""
    inizio = (pagina or "").find(f'class="server active" data-name="{SERVER_DIRETTO}"')
    if inizio < 0:
        inizio = (pagina or "").find(f'class="server" data-name="{SERVER_DIRETTO}"')
    if inizio < 0:
        return []
    fine = pagina.find('<div class="server', inizio + 10)
    blocco = pagina[inizio:fine if fine > 0 else len(pagina)]
    visti, puntate = set(), []
    for id_puntata, numero in RE_PUNTATA.findall(blocco):
        if id_puntata not in visti:
            visti.add(id_puntata)
            puntate.append({"id": id_puntata, "numero": numero.strip()})
    return puntate


def leggi_anno(tooltip: str) -> str:
    m = RE_ANNO.search(tooltip or "")
    return m.group(1) if m else ""


# --- il sito -------------------------------------------------------------------

class AnimeWorld:
    solo_anime = True
    nome = "AnimeWorld"

    def __init__(self, config) -> None:
        self.config = config
        self._client: httpx.AsyncClient | None = None

    @property
    def attivo(self) -> bool:
        return bool(self.config.animeworld_url)

    @property
    def base(self) -> str:
        return self.config.animeworld_url

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

    async def _get(self, percorso: str, **kw) -> httpx.Response:
        try:
            r = await self.client().get(f"{self.base}{percorso}", **kw)
        except httpx.HTTPError as exc:
            raise ErroreServizio(
                "AnimeWorld non risponde.",
                "Il sito cambia spesso indirizzo: se ora è un altro, mettilo in ANIMEWORLD_URL nel .env.") from exc
        if r.status_code in (403, 503):
            raise ErroreServizio("AnimeWorld blocca la richiesta (protezione anti-bot).", "Riprova tra qualche minuto.")
        if not r.is_success:
            raise ErroreServizio(f"AnimeWorld ha risposto con un errore ({r.status_code}).", "Riprova più tardi.")
        return r

    async def cerca(self, titolo: str) -> list[dict]:
        if not self.attivo:
            raise ErroreServizio("AnimeWorld non è attivo.", "Metti l'indirizzo del sito in ANIMEWORLD_URL nel .env.")
        voci = leggi_ricerca((await self._get("/search", params={"keyword": titolo})).text)[:12]

        async def anno(v: dict) -> None:
            if v["tooltip"]:
                try:
                    v["anno"] = leggi_anno((await self._get(f"/api/tooltip/{v['tooltip']}")).text)
                except ErroreServizio:
                    pass

        # l'anno serve a mettere in ordine le stagioni (la prima prima della seconda)
        await asyncio.gather(*(anno(v) for v in voci if v["tipo"] in ("TV", "ONA")))
        return voci

    async def puntate(self, anime_id: str) -> list[dict]:
        return leggi_puntate((await self._get(f"/play/{anime_id}")).text)

    async def link_video(self, puntata_id: str) -> tuple[str, dict]:
        try:
            dati = (await self._get("/api/episode/info", params={"id": puntata_id})).json()
        except ValueError as exc:
            raise ErroreServizio("AnimeWorld non dà il file di questa puntata.", "Riprova più tardi.") from exc
        url = str(dati.get("grabber") or "")
        if not url.startswith("http"):
            raise ErroreServizio("Questa puntata su AnimeWorld non si può scaricare.", "Ci pensa un'altra fonte.")
        return url, {"Referer": f"{self.base}/", "User-Agent": AGENTE_BROWSER}
