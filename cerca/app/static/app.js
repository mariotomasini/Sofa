// Cerca — script delle pagine. Niente build: le animazioni e i componenti animati sono in movimento.js.
"use strict";

const BASE = window.BASE || "";

// --- utilità -------------------------------------------------------------
function esc(testo) {
  return String(testo ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

function dimensione(byte) {
  if (byte === 0) return "0 B";
  if (!byte) return "?";
  const unita = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = byte;
  while (v >= 1024 && i < unita.length - 1) { v /= 1024; i++; }
  return (i >= 3 ? v.toFixed(1).replace(".", ",") : Math.round(v)) + " " + unita[i];
}

function avviso(testo, errore = false) {
  mostraAvviso(testo, errore ? "errore" : "pronto", errore ? 8000 : 5000);
}

// Icone a linea (una famiglia, un tratto): al posto dei simboli e delle emoji
const ICONE = {
  scarica: '<svg class="icona" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11m0 0-4-4m4 4 4-4M5 20h14"/></svg>',
  play: '<svg class="icona" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5v13l11-6.5z"/></svg>',
  info: '<svg class="icona" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 8h.01"/></svg>',
  nota: '<svg class="icona" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/></svg>',
  lente: '<svg class="icona" viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6"/><path d="m15 15 5 5"/></svg>',
  audioSpento: '<svg class="icona spento" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="m16 9.5 5 5m0-5-5 5"/></svg>',
  audioAcceso: '<svg class="icona acceso" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="M16 9a4 4 0 0 1 0 6M18.5 6.5a7.5 7.5 0 0 1 0 11"/></svg>',
};

// Dopo questo tempo smettiamo di aspettare e diciamo cosa succede (niente caricamenti infiniti)
const ATTESA_MASSIMA = 45000;

async function chiama(percorso, opzioni = {}) {
  let risposta;
  const controllo = new AbortController();
  const timer = setTimeout(() => controllo.abort(), opzioni.attesa || ATTESA_MASSIMA);
  try {
    risposta = await fetch(BASE + percorso, { ...opzioni, signal: controllo.signal });
  } catch (e) {
    throw e.name === "AbortError"
      ? { errore: "Cerca ci sta mettendo troppo a rispondere.", cosa_fare: "Un servizio è lento o spento.", diagnosi: true }
      : { errore: "Cerca non risponde.", cosa_fare: "Controlla la connessione e ricarica la pagina." };
  } finally {
    clearTimeout(timer);
  }
  let dati = {};
  try { dati = await risposta.json(); } catch (e) { /* risposta non JSON */ }
  if (risposta.status === 401 && dati.accesso) {
    // l'accesso non vale più (cookie tolto o cambiato): si torna a "Chi guarda?"
    location.href = BASE + "/chi-guarda?dopo=" + encodeURIComponent(location.pathname + location.search);
  }
  if (!risposta.ok) {
    throw dati.errore ? dati : { errore: "Qualcosa è andato storto (" + risposta.status + ").", cosa_fare: "Riprova tra poco." };
  }
  return dati;
}

function inviaJSON(percorso, corpo) {
  return chiama(percorso, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(corpo),
  });
}

function htmlErrore(e) {
  return `<div class="errore"><strong>${esc(e.errore || "Errore")}</strong>` +
    (e.cosa_fare ? `<p>${esc(e.cosa_fare)}</p>` : "") +
    `<p><a href="${BASE}/diagnosi">Controlla quali servizi rispondono</a></p></div>`;
}

// Segnaposto per i film senza locandina: titolo in alto, anno in basso
function htmlSenzaPoster(titolo, anno) {
  return `<span class="senza-poster"><span class="titolo">${esc(titolo)}</span><span class="anno">${esc(anno || "")}</span></span>`;
}

// --- pagina del film ------------------------------------------------------
function avviaFilm(tmdbId) {
  const provider = document.querySelector("#provider .contenuto");
  if (provider) {
    chiama(`/api/film/${tmdbId}/provider`)
      .then((d) => { provider.innerHTML = htmlProvider(d); })
      .catch((e) => { provider.innerHTML = htmlErrore(e); });
  }

  // Un solo tasto: Radarr sceglie la versione migliore (prima ITA) e fa tutto da solo.
  // Dopo il clic il tasto lascia il posto allo stato: in coda, in download, cerco, pronto.
  const auto = document.getElementById("scarica-auto");
  const statoBox = document.getElementById("stato-film");
  const testoScarica = auto ? auto.innerHTML : "";
  let ultimoStato = "";
  async function aggiornaStato() {
    if (!statoBox) return;
    try {
      const d = await chiama(`/api/film/${tmdbId}/stato`);
      const html = htmlStatoFilm(d);
      if (html !== ultimoStato) {  // ridisegna solo se cambia: le animazioni non ripartono ogni 5 secondi
        ultimoStato = html;
        statoBox.innerHTML = html;
        preparaTieni(statoBox, (b) => azioneAutomatico(b, aggiornaStato));
      } else {
        const p = statoBox.querySelector(".pellicola");
        if (p && d.percentuale != null) p.style.setProperty("--p", `${d.percentuale}%`);
      }
      auto.hidden = d.stato !== "nessuno";
      auto.disabled = false;
      auto.innerHTML = testoScarica;
    } catch (e) { /* Radarr spento: resta il tasto */ }
  }
  if (auto) {
    aggiornaStato();
    setInterval(aggiornaStato, 5000);
    auto.addEventListener("click", async () => {
      auto.disabled = true;
      try {
        await conAvviso(inviaJSON("/api/scarica/auto", { tmdb_id: tmdbId }), "Lo aggiungo ai download");
        aggiornaStato();
      } catch (e) { auto.disabled = false; }
    });
    statoBox.addEventListener("click", (ev) => {
      const b = ev.target.closest("button[data-tipo]:not([data-tieni])");
      if (b) azioneAutomatico(b, aggiornaStato);
    });
  }

  const avvisami = document.getElementById("avvisami");
  if (avvisami) avvisami.addEventListener("click", async () => {
    avvisami.disabled = true;
    try {
      const r = await chiama(`/api/avvisami/${tmdbId}`, { method: "POST" });
      avvisami.setAttribute("aria-pressed", r.attivo ? "true" : "false");
      avvisami.querySelector("span").textContent = r.attivo ? "Ti avviso se diventa gratis" : "Avvisami se diventa gratis";
      if (r.attivo) suonaCampanella(avvisami.querySelector("svg"));
      avviso(r.messaggio);
    } catch (e) { avviso(e.errore, true); }
    avvisami.disabled = false;
  });
}

// "Trailer": si apre grande, con l'audio (dalla scheda del film e sul telefono)
document.addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-apri-trailer]");
  if (b) { ev.preventDefault(); apriTrailer(b.dataset.apriTrailer); }
});

// Logo del servizio, con l'iniziale al suo posto se l'immagine manca o non si carica
function htmlLogo(p) {
  const iniziale = `<span class="logo-iniziale" aria-hidden="true">${esc((p.nome || "?").charAt(0))}</span>`;
  if (!p.logo) return iniziale;
  return `<img src="${esc(p.logo)}" alt="" loading="lazy" ` +
    `onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'logo-iniziale',textContent:this.dataset.i}))" ` +
    `data-i="${esc((p.nome || "?").charAt(0))}">`;
}

const BREVE_OFFERTA = { ads: "gratis", free: "gratis", flatrate: "abbonamento", rent: "noleggio", buy: "acquisto" };

function htmlProvider(d) {
  if (d.senza_chiave) {
    // senza chiave TMDB non sappiamo dove si guarda: rimandiamo a JustWatch
    return "<p>Per sapere su quali servizi si guarda in Italia, cercalo su JustWatch.</p>" +
      `<a class="servizio" href="${esc(d.link)}" target="_blank" rel="noopener">` +
      '<span class="logo-iniziale" aria-hidden="true">J</span><span class="nome-servizio">Cerca su JustWatch</span>' +
      '<span class="freccia" aria-hidden="true">↗</span></a>' +
      '<p class="sottotitolo">Con la chiave TMDB facoltativa (Impostazioni di mediabox) lo vedi direttamente qui.</p>';
  }
  if (!d.gruppi.length) {
    return "<p>Al momento non risulta disponibile su nessun servizio in Italia.</p>" +
      "<p class=\"sottotitolo\">Puoi usare il pulsante \"Avvisami\" qui sopra.</p>";
  }
  let html = "";
  for (const g of d.gruppi) {
    const gratis = g.tipo === "ads" || g.tipo === "free";
    html += `<div class="gruppo-provider${gratis ? " gratis" : ""}"><h3>${esc(g.etichetta)}</h3><div class="loghi">`;
    for (const p of g.provider) {
      html += `<a class="servizio" href="${esc(d.link)}" target="_blank" rel="noopener">${htmlLogo(p)}` +
        `<span class="nome-servizio">${esc(p.nome)}<span class="offerta"> · ${BREVE_OFFERTA[g.tipo] || ""}</span></span>` +
        `<span class="freccia" aria-hidden="true">↗</span></a>`;
    }
    html += "</div></div>";
  }
  return html;
}

// --- ricerca di un film da associare (torrent manuali) ------------------------
function cercatoreFilm(campo, bottone, contenitore, quandoScelto) {
  async function cerca() {
    const q = campo.value.trim();
    if (!q) return;
    contenitore.innerHTML = '<p class="caricamento">Cerco</p>';
    try {
      const d = await chiama(`/api/tmdb?q=${encodeURIComponent(q)}`);
      if (!d.film.length) { contenitore.innerHTML = "<p>Nessun film trovato.</p>"; return; }
      contenitore.innerHTML = d.film.slice(0, 8).map((f) =>
        `<button type="button" class="scelta" data-id="${f.id}">` +
        (f.poster ? `<img src="${esc(f.poster)}" alt="">` : "") +
        `<span>${esc(f.titolo)} ${f.anno ? "(" + esc(f.anno) + ")" : ""}</span></button>`).join("");
    } catch (e) { contenitore.innerHTML = htmlErrore(e); }
  }
  bottone.addEventListener("click", cerca);
  campo.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); cerca(); } });
  contenitore.addEventListener("click", (ev) => {
    const b = ev.target.closest("button.scelta");
    if (!b) return;
    contenitore.querySelectorAll(".scelta").forEach((x) => x.classList.remove("scelto"));
    b.classList.add("scelto");
    quandoScelto(Number(b.dataset.id), b.textContent.trim());
  });
}

// --- pagina "Aggiungi torrent" ------------------------------------------------------
function avviaTorrent() {
  const form = document.getElementById("form-torrent");
  const tmdb = document.getElementById("tmdb_id");
  const file = document.getElementById("file");
  file.addEventListener("change", () => {
    document.getElementById("nome-file").textContent = file.files.length ? file.files[0].name : "Nessun file scelto";
  });
  cercatoreFilm(
    document.getElementById("cerca-film"),
    document.getElementById("bottone-cerca-film"),
    document.getElementById("scelte-film"),
    (id) => { tmdb.value = id; },
  );
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const bottone = form.querySelector("button[type=submit]");
    bottone.disabled = true;
    try {
      const r = await chiama("/api/torrent", { method: "POST", body: new FormData(form) });
      avviso(r.messaggio);
      setTimeout(() => { window.location.href = `${BASE}/download`; }, 1200);
    } catch (e) {
      avviso(e.errore + " " + (e.cosa_fare || ""), true);
      bottone.disabled = false;
    }
  });
}

// --- elenco download (pagina Download e home) -----------------------------------------
function htmlDownload(d, compatto = false) {
  const fonte = { archive: "Internet Archive", prowlarr: "Indexer", manuale: "Torrent manuale" }[d.fonte] || d.fonte;
  if (compatto) {
    const riga = d.stato === "in_corso"
      ? `${Math.round(d.percentuale)}% · ${esc(d.scaricati_testo)} di ${esc(d.totale_testo)}${d.velocita_testo ? " · " + esc(d.velocita_testo) : ""}`
      : esc(d.stato_testo);
    return `<a class="download compatto stato-${d.stato}" href="${BASE}/download">` +
      (d.poster ? `<img src="${esc(d.poster)}" alt="">` : htmlSenzaPoster(d.titolo, d.anno)) +
      `<span class="descrizione"><strong>${esc(d.titolo)}${d.anno ? " (" + esc(d.anno) + ")" : ""}</strong>` +
      `${htmlPellicola(d.percentuale, { viva: d.stato === "in_corso" })}<span class="sottotitolo">${riga}</span></span></a>`;
  }
  const barra = d.stato === "in_corso"
    ? htmlPellicola(d.percentuale, { viva: true }) +
      `<span class="sottotitolo">${Math.round(d.percentuale)}% · ${esc(d.scaricati_testo)} di ${esc(d.totale_testo)}` +
      `${d.velocita_testo ? " · " + esc(d.velocita_testo) : ""}</span>`
    : "";
  let azioni = "";
  if (d.anticipabile) azioni += `<button class="secondario" data-prima="${esc(d.hash)}">Scarica prima questa</button>`;
  if (d.stato === "da_associare") azioni += `<button data-associa="${d.id}" data-nome="${esc(d.titolo)}">Scegli il film</button>`;
  if (d.stato === "errore") azioni += `<button data-riprova="${d.id}">Riprova</button>`;
  if (d.stato === "errore") azioni += `<button class="secondario" data-associa="${d.id}" data-nome="${esc(d.titolo)}">Cambia film</button>`;
  if (d.stato !== "importazione") {
    const etichetta = d.stato === "importato" ? "Togli dall'elenco" : "Annulla";
    azioni += `<button class="secondario" data-tieni data-annulla="${d.id}" data-stato="${d.stato}">${etichetta}</button>`;
  }
  return `<div class="download stato-${d.stato}">` +
    (d.poster ? `<img src="${esc(d.poster)}" alt="">` : htmlSenzaPoster(d.titolo, d.anno)) +
    `<div class="descrizione"><strong>${esc(d.titolo)}${d.anno ? " (" + esc(d.anno) + ")" : ""}</strong>` +
    `<span class="riga-stato"><span class="stato">${iconaAnimata(ICONA_DOWNLOAD[d.stato] || "")}${esc(d.stato_testo)}</span> <span class="sottotitolo">· ${esc(fonte)}</span></span>` +
    barra + (d.messaggio ? `<p class="messaggio">${esc(d.messaggio)}</p>` : "") +
    `</div><div class="azioni">${azioni}</div></div>`;
}

const ICONA_DOWNLOAD = { in_corso: "download", completato: "importazione", importazione: "importazione", importato: "pronto",
  da_associare: "problema", errore: "problema" };

// Download di Radarr/Sonarr: la lingua sempre in vista, in giallo se non è quella preferita
function etichettaLingua(x) {
  if (x.senza_lingua || !x.lingua) return "";
  if (x.preferita) return `<span class="lingua">${esc(x.lingua)}</span>`;
  return `<span class="lingua altra">${esc(x.lingua)} · non in ${esc(x.lingua_preferita.toLowerCase())}</span>`;
}

function htmlStatoAutomatico(x) {
  const icona = iconaAnimata(x.stato);
  let html = `<span class="riga-stato stato-auto-${esc(x.stato || "")}"><span class="stato">${icona}${esc(x.stato_testo || "")}</span></span>`;
  if (x.stato === "download") {
    const pezzi = [`${Math.round(x.percentuale)}%${x.dimensione ? " di " + dimensione(x.dimensione) : ""}`];
    if (x.velocita) pezzi.push(`${dimensione(x.velocita)}/s`);
    if (x.rimanente) pezzi.push(`mancano ${tempoRimanente(x.rimanente)}`);
    html += htmlPellicola(x.percentuale, { viva: true, grande: true }) +
      `<span class="sottotitolo">${pezzi.map(esc).join(" · ")}</span>`;
  }
  return html;
}

function tempoRimanente(s) {
  if (s < 60) return "meno di un minuto";
  if (s < 3600) return `${Math.round(s / 60)} min`;
  const ore = Math.floor(s / 3600), min = Math.round((s % 3600) / 60);
  return ore >= 24 ? `${Math.round(ore / 24)} giorni` : `${ore} h${min ? " " + min + " min" : ""}`;
}

function etichettaFonte(x) {
  if (!x.fonte) return "";
  return `<span class="fonte fonte-${x.fonte.toLowerCase()}">${x.fonte === "Telegram" ? "Telegram" : "Torrent"}</span>`;
}

function htmlAutomatico(x, inCorso) {
  const nota = !inCorso && !x.preferita
    ? `<span class="sottotitolo">Lo sostituisco da solo appena esce in ${esc(x.lingua_preferita.toLowerCase())}.</span>` : "";
  const dati = `data-tipo="${esc(x.tipo)}" data-ids="${esc((x.ids || []).join(","))}"`;
  const azioni = inCorso
    ? (x.anticipabile ? `<button class="secondario" data-prima="${esc(x.hash)}">Scarica prima questa</button>` : "") +
      (x.ricercabile ? `<button class="secondario" data-riprova ${dati}>Cerca di nuovo</button>` : "") +
      (x.annullabile ? `<button class="secondario" data-tieni data-annulla-auto ${dati}>Annulla</button>` : "")
    : "";
  const altra = !x.senza_lingua && x.lingua && !x.preferita;
  return `<article class="download${altra ? " non-preferita" : ""}"><div class="descrizione">` +
    `<strong>${etichettaLingua(x)}${etichettaFonte(x)} ${esc(x.titolo)}</strong>` +
    (inCorso ? htmlStatoAutomatico(x) : "") +
    (inCorso && x.anticipabile ? `<span class="sottotitolo">${x.posto}° in coda</span>` : "") +
    (x.dettaglio ? `<span class="sottotitolo">${esc(x.dettaglio)}</span>` : "") + nota +
    `</div>${azioni ? `<div class="azioni">${azioni}</div>` : ""}</article>`;
}

async function azioneAutomatico(b, dopo) {
  const corpo = { tipo: b.dataset.tipo, ids: b.dataset.ids.split(",").filter(Boolean).map(Number) };
  // "Annulla" si conferma tenendo premuto il tasto (smetto di cercarlo e fermo il download; quello in Jellyfin resta)
  b.disabled = true;
  try {
    const percorso = b.dataset.annullaAuto !== undefined ? "/api/automatici/annulla" : "/api/automatici/riprova";
    avviso((await inviaJSON(percorso, corpo)).messaggio);
    if (dopo) dopo();
  } catch (e) { avviso(e.errore + " " + (e.cosa_fare || ""), true); b.disabled = false; }
}

// Scheda film: il tasto Download diventa lo stato del film (in coda, in download, cerco, pronto)
function htmlStatoFilm(d) {
  if (!d || d.stato === "nessuno") return "";
  const dati = `data-tipo="film" data-ids="${d.id}"`;
  let azioni = "";
  if (["cerco"].includes(d.stato)) azioni += `<button class="secondario" data-riprova ${dati}>Cerca di nuovo</button>`;
  if (d.stato !== "pronto" || d.migliorabile) azioni += `<button class="secondario" data-tieni data-annulla-auto ${dati}>Annulla</button>`;
  const nota = d.stato === "pronto" && d.migliorabile
    ? `<span class="sottotitolo">Cerco ancora la versione in ${esc(d.lingua_preferita.toLowerCase())}: la sostituisco da sola.</span>` : "";
  return `<div class="stato-film"><span>${etichettaLingua(d)}${etichettaFonte(d)}</span>${htmlStatoAutomatico(d)}${nota}` +
    `${azioni ? `<div class="azioni-riga">${azioni}</div>` : ""}</div>`;
}

// --- pagina Download: una copertina per film o serie, lo stato di ogni voce toccandola ------------
const COLORE_STATO = { problema: "problema", download: "attivo", importazione: "attivo", coda: "attesa",
  da_associare: "problema", cerco: "attesa", non_uscito: "attesa", attesa: "attesa", pronto: "pronto" };

function htmlSchedaDownload(g) {
  const copertina = g.poster ? `<img src="${esc(g.poster)}" alt="" loading="lazy">` : htmlSenzaPoster(g.titolo, "");
  const barra = g.stato === "download" || g.stato === "importazione"
    ? htmlPellicola(g.percentuale, { viva: true }) : "";
  const quante = g.tipo === "serie" && g.quante > 1 ? `<span class="conta-puntate">${g.quante}</span>` : "";
  return `<button type="button" class="scheda-download colore-${COLORE_STATO[g.stato] || "attesa"}" data-gruppo="${esc(g.chiave)}" data-stato="${esc(g.stato)}">` +
    `<span class="poster">${copertina}${quante}</span>` +
    `<span class="titolo">${esc(g.titolo)}</span>` +
    `<span class="stato-breve">${iconaAnimata(g.stato)}${esc(g.stato_testo)}</span>${barra}</button>`;
}

// In cima alla pagina Download: quello che sta scaricando adesso, con la velocità dal vivo
function htmlOra(g) {
  const copertina = g.poster ? `<img src="${esc(g.poster)}" alt="">` : htmlSenzaPoster(g.titolo, "");
  return `<div class="copertina">${copertina}</div><div>` +
    `<span class="stato-breve colore-attivo" style="color:var(--lampada)">${iconaAnimata("download")}In download</span>` +
    `<h2>${esc(g.titolo)}</h2>${htmlPellicola(g.percentuale, { viva: true, grande: true })}` +
    `<div class="cifre"><span><b class="num" data-ora="percentuale">${Math.round(g.percentuale || 0)}%</b></span>` +
    `<span class="num" data-ora="rimanente">${g.rimanente ? `mancano ${esc(tempoRimanente(g.rimanente))}` : ""}</span></div></div>` +
    `<div class="velocita" aria-hidden="true"><span class="velocita-nome">Velocità</span><div class="grafico-velocita"></div></div>`;
}

function htmlVoceDownload(v) {
  if (v.origine === "cerca") return htmlDownload(v);
  return htmlAutomatico({ ...v, titolo: v.voce || v.titolo }, v.origine === "auto");
}

function avviaPaginaDownload() {
  const griglia = document.getElementById("griglia-download");
  const dettaglio = document.getElementById("dettaglio-download");
  let gruppi = [];

  function disegnaDettaglio() {
    const g = gruppi.find((x) => x.chiave === dettaglio.dataset.gruppo);
    if (!g) { if (dettaglio.open) dettaglio.close(); return; }
    document.getElementById("dettaglio-copertina").innerHTML = g.poster ? `<img src="${esc(g.poster)}" alt="">` : "";
    document.getElementById("dettaglio-titolo").textContent = g.titolo;
    const pezzi = [g.stato_testo];
    if (g.velocita) pezzi.push(`${dimensione(g.velocita)}/s`);
    if (g.rimanente) pezzi.push(`mancano ${tempoRimanente(g.rimanente)}`);
    document.getElementById("dettaglio-stato").textContent = pezzi.join(" · ");
    const voci = document.getElementById("dettaglio-voci");
    const html = g.voci.map(htmlVoceDownload).join("");
    if (voci._html !== html) {
      voci._html = html;
      voci.innerHTML = html;
      preparaTieni(voci, eseguiVoce);
    }
  }

  const ora = document.getElementById("ora");
  const riassunto = document.getElementById("riassunto-download");
  let chiaveOra = "", linea = null, ultimaGriglia = "";

  function disegnaOra() {
    const g = gruppi.find((x) => x.stato === "download");
    ora.hidden = !g;
    if (!g) { chiaveOra = ""; return; }
    if (g.chiave !== chiaveOra) {
      chiaveOra = g.chiave;
      ora.innerHTML = htmlOra(g);
      linea = lineaDalVivo(ora.querySelector(".grafico-velocita"), { formato: (v) => `${dimensione(v)}/s` });
    }
    const p = ora.querySelector(".pellicola");
    p.style.setProperty("--p", `${g.percentuale || 0}%`);
    ora.querySelector('[data-ora="percentuale"]').textContent = `${Math.round(g.percentuale || 0)}%`;
    ora.querySelector('[data-ora="rimanente"]').textContent = g.rimanente ? `mancano ${tempoRimanente(g.rimanente)}` : "";
    linea.aggiungi(g.velocita || 0);
  }

  function disegnaRiassunto() {
    const conta = (stati) => gruppi.filter((g) => stati.includes(g.stato)).length;
    const pezzi = [[conta(["download"]), "in download"], [conta(["coda", "cerco", "attesa"]), "in attesa"],
      [conta(["problema", "da_associare"]), "da sistemare"], [conta(["pronto"]), "pronti"]].filter(([n]) => n);
    riassunto.innerHTML = pezzi.map(([n, t]) => `<span><span class="num">${n}</span> ${t}</span>`).join("");
  }

  async function aggiorna() {
    try {
      const d = await chiama("/api/download/gruppi");
      gruppi = d.gruppi;
      const html = gruppi.length ? gruppi.map(htmlSchedaDownload).join("")
        : `<div class="salotto-vuoto">${SALOTTO}<h2>Niente in download</h2>` +
          `<p>Cerca un film e premi Scarica: lo ritrovi in Jellyfin, pronto da guardare sulla TV.</p>` +
          `<a class="bottone" href="${BASE}/">${ICONE.lente}Cerca un film</a></div>`;
      if (html !== ultimaGriglia) {  // ridisegna solo se qualcosa cambia: le animazioni restano fluide
        ultimaGriglia = html;
        griglia.innerHTML = html;
        griglia.classList.toggle("griglia-download", gruppi.length > 0);
        accendiSalotto(griglia.querySelector(".salotto"));
      }
      document.getElementById("cerca-ora").parentElement.hidden = !gruppi.length;
      disegnaOra();
      disegnaRiassunto();
      if (dettaglio.open) disegnaDettaglio();
    } catch (e) { if (!gruppi.length) griglia.innerHTML = htmlErrore(e); }
  }

  griglia.addEventListener("click", (ev) => {
    const b = ev.target.closest("button[data-gruppo]");
    if (!b) return;
    dettaglio.dataset.gruppo = b.dataset.gruppo;
    disegnaDettaglio();
    dettaglio.showModal();
  });
  document.getElementById("dettaglio-chiudi").addEventListener("click", () => dettaglio.close());

  // i pulsanti delle voci: quelle di Radarr/Sonarr hanno data-tipo, quelle di Cerca il numero del download.
  // "Annulla" e "Togli dall'elenco" si confermano tenendo premuto (movimento.js: tieniPremuto).
  document.getElementById("dettaglio-voci").addEventListener("click", (ev) => {
    const b = ev.target.closest("button:not([data-tieni])");
    if (b) eseguiVoce(b);
  });

  async function eseguiVoce(b) {
    if (b.dataset.prima) {
      b.disabled = true;
      try { avviso((await inviaJSON("/api/download/prima", { hash: b.dataset.prima })).messaggio); aggiorna(); }
      catch (e) { avviso(e.errore + " " + (e.cosa_fare || ""), true); b.disabled = false; }
      return;
    }
    if (b.dataset.tipo) { await azioneAutomatico(b, aggiorna); return; }
    try {
      if (b.dataset.riprova) {
        avviso((await chiama(`/api/download/${b.dataset.riprova}/riprova`, { method: "POST" })).messaggio);
      } else if (b.dataset.annulla) {
        // tolto dall'elenco: quello che è in Jellyfin resta; annullato: i file scaricati finora si cancellano
        avviso((await chiama(`/api/download/${b.dataset.annulla}/annulla`, { method: "POST" })).messaggio);
      } else if (b.dataset.associa) {
        dettaglio.close();
        apriAssocia(Number(b.dataset.associa), b.dataset.nome);
      }
      aggiorna();
    } catch (e) { avviso(e.errore + " " + (e.cosa_fare || ""), true); }
  }

  document.getElementById("cerca-ora").addEventListener("click", async (ev) => {
    ev.target.disabled = true;
    try { avviso((await chiama("/api/automatici/cerca", { method: "POST" })).messaggio); }
    catch (e) { avviso(e.errore + " " + (e.cosa_fare || ""), true); ev.target.disabled = false; }
  });

  function apriAssocia(idDownload, nome) {
    const dialogo = document.getElementById("dialogo-associa");
    document.getElementById("associa-nome").textContent = nome;
    const campo = document.getElementById("associa-cerca");
    campo.value = nome.replace(/[._]/g, " ").replace(/\b(1080p|720p|2160p|x264|x265|h264|bluray|web-?dl|ita|eng)\b.*$/i, "").trim();
    dialogo.dataset.download = idDownload;
    document.getElementById("associa-scelte").innerHTML = "";
    dialogo.showModal();
  }
  cercatoreFilm(
    document.getElementById("associa-cerca"),
    document.getElementById("associa-bottone"),
    document.getElementById("associa-scelte"),
    async (tmdbId) => {
      const dialogo = document.getElementById("dialogo-associa");
      try {
        const r = await inviaJSON(`/api/download/${dialogo.dataset.download}/associa`, { tmdb_id: tmdbId });
        avviso(r.messaggio);
        dialogo.close();
        aggiorna();
      } catch (e) { avviso(e.errore, true); }
    },
  );

  aggiorna();
  setInterval(aggiorna, 4000);
}

// --- novità nelle pagine iniziali dei mondi -------------------------------------------
// Il trailer al passaggio: film e serie con l'id di TMDB (le serie di TVmaze non ce l'hanno)
function chiaveTrailer(f) {
  if (f.sottotitolo || !f.id) return "";
  if (f.serie) return "generi_tvmaze" in f ? "" : `tv:${f.id}`;
  return `movie:${f.id}`;
}
function attrTrailer(f) {
  const k = chiaveTrailer(f);
  return k ? ` data-trailer="${esc(k)}"` : "";
}
function votoBreve(v) {
  return v ? Number(v).toFixed(1).replace(".", ",") : "";
}

function htmlScheda(f) {
  let link = `${BASE}/film/${f.id}`;
  let sotto = esc([f.anno, votoBreve(f.voto)].filter(Boolean).join(" · "));
  let dati = "";
  if (f.serie) {
    link = `/sonarr/add/new?term=${encodeURIComponent(f.titolo_originale || f.titolo)}`;
    dati = attrSerie(f);
  }
  if (f.sottotitolo) {  // episodio da Sonarr
    link = "/jellyfin/";
    sotto = `${esc(f.sottotitolo)}${f.pronto ? ' · <span class="pronto">pronto</span>' : ""}`;
  }
  return `<a class="scheda" href="${link}"${dati}${attrTrailer(f)}><span class="poster">` +
    (f.poster ? `<img src="${esc(f.poster)}" alt="" loading="lazy">` : htmlSenzaPoster(f.titolo, f.anno)) +
    htmlEtichette(f) + "</span>" +
    `<span class="titolo">${esc(f.titolo)}</span><span class="anno">${sotto}</span></a>`;
}

async function avviaNovita(mondo = "film") {
  const sezione = document.getElementById("novita");
  try {
    const d = await chiama(`/api/novita?mondo=${encodeURIComponent(mondo)}`);
    const prima = document.getElementById("tendenze");
    const seconda = document.getElementById("cinema");
    prima.innerHTML = d.prima.map(htmlScheda).join("");
    seconda.innerHTML = d.seconda.map(htmlScheda).join("");
    prima.previousElementSibling.hidden = prima.hidden = !d.prima.length;
    seconda.previousElementSibling.hidden = seconda.hidden = !d.seconda.length;
    sezione.hidden = !(d.prima.length || d.seconda.length);
  } catch (e) {
    sezione.hidden = true;  // senza TMDB la pagina resta comunque usabile
  }
}

// --- home in stile Netflix --------------------------------------------------------------
// Fila dei generi sotto la ricerca (in Kids solo generi adatti ai bambini)
async function avviaGeneri() {
  const nav = document.getElementById("generi");
  if (!nav) return;
  const mondo = nav.dataset.mondo;
  try {
    const d = await chiama(`/api/generi?mondo=${encodeURIComponent(mondo)}`);
    nav.innerHTML = d.generi.map((g) =>
      `<a class="genere" href="${BASE}/genere/${g.id}?mondo=${encodeURIComponent(d.mondo)}">${esc(g.nome)}</a>`).join("");
    nav.hidden = !d.generi.length;
  } catch (e) {
    nav.hidden = true;
  }
}

// Top 10: il posto è un numero piccolo sulla locandina, niente numeroni contornati
function htmlTop(f, i) {
  const link = f.serie ? `/sonarr/add/new?term=${encodeURIComponent(f.titolo_originale || f.titolo)}` : `${BASE}/film/${f.id}`;
  return `<a class="scheda-top" href="${link}"${f.serie ? attrSerie(f) : ""}${attrTrailer(f)} aria-label="${i + 1}. ${esc(f.titolo)}">` +
    `<span class="poster-top"><span class="numero num" aria-hidden="true">${i + 1}</span>` +
    (f.poster ? `<img src="${esc(f.poster)}" alt="" loading="lazy">` : htmlSenzaPoster(f.titolo, f.anno)) +
    (f.nuovo ? '<span class="nastro">Aggiunto di recente</span>' : "") + "</span>" +
    `<span class="titolo">${esc(f.titolo)}</span><span class="anno">${esc([f.anno, votoBreve(f.voto)].filter(Boolean).join(" · "))}</span></a>`;
}

// Jellyfin non ha l'immagine: al suo posto il segnaposto con il titolo
function posterMancante(img) {
  img.outerHTML = htmlSenzaPoster(img.dataset.titolo, img.dataset.anno);
}

function htmlContinua(e) {
  const percentuale = Math.max(3, Math.min(100, e.percentuale || 0));
  return `<a class="scheda scheda-continua" href="${esc(e.link)}">` +
    `<img src="${esc(e.poster)}" alt="" loading="lazy" data-titolo="${esc(e.titolo)}" data-anno="${esc(e.anno)}" onerror="posterMancante(this)">` +
    (e.percentuale ? `<span class="avanzamento" role="progressbar" aria-valuenow="${e.percentuale}" aria-valuemin="0" aria-valuemax="100" aria-label="Visto al ${e.percentuale}%"><span style="width:${percentuale}%"></span></span>` : "") +
    `<span class="titolo">${esc(e.titolo)}</span><span class="anno">${esc(e.sottotitolo || (e.percentuale ? `visto al ${e.percentuale}%` : "da iniziare"))}</span></a>`;
}

function htmlRiga(r, mondo) {
  let schede;
  if (r.tipo === "top10") schede = r.elementi.map(htmlTop).join("");
  else if (r.tipo === "continua") schede = r.elementi.map(htmlContinua).join("");
  else schede = r.elementi.map(htmlScheda).join("");
  let tutti = "";
  if (r.genere) tutti = `${BASE}/genere/${r.genere}?mondo=${encodeURIComponent(mondo)}`;
  if (r.piattaforma) tutti = `${BASE}/piattaforma/${r.piattaforma}?mondo=${encodeURIComponent(mondo)}`;
  const link = tutti ? `<a class="link-accento" href="${tutti}"${r.piattaforma && r.codice ? ` data-tema="${esc(r.codice)}"` : ""}>Vedi tutti</a>` : "";
  const id = r.ancora || `riga-${r.tipo}-${r.genere || r.piattaforma || ""}`;
  const logo = r.logo ? `<img class="logo-piattaforma" src="${esc(r.logo)}" alt="" width="36" height="36" onerror="this.remove()">` : "";
  return `<section class="riga riga-${esc(r.tipo)}" aria-labelledby="${id}">` +
    `<div class="riga-testa"><h2 id="${id}">${logo}${esc(r.titolo)}</h2>${link}</div>` +
    `<div class="striscia${r.tipo === "top10" ? " striscia-top" : ""}">${schede}</div></section>`;
}

async function avviaHome(mondo = "film") {
  const contenitore = document.getElementById("righe");
  if (!contenitore) return;
  avviaChipPiattaforme(mondo);
  const disegna = (d) => {
    const top = (d.righe.find((r) => r.tipo === "top10") || {}).elementi || [];
    contenitore.innerHTML = (top.length ? htmlInEvidenza({ ...top[0], top10: 1 }, mondo, "") : "") +
      d.righe.map((r) => htmlRiga(r, mondo)).join("") +
      (d.errore && !d.righe.length ? htmlErrore(d.errore) : "") +
      `<div class="centrato"><button type="button" class="secondario" id="scegli-piattaforme">Scegli le tue piattaforme</button></div>`;
    document.getElementById("scegli-piattaforme").addEventListener("click", () => apriScegliPiattaforme(mondo));
    contenitore.removeAttribute("aria-busy");
    entraVetrina(contenitore);
  };
  await conMemoria(`home:${mondo}`, `/api/home?mondo=${encodeURIComponent(mondo)}`, disegna, (e) => {
    contenitore.innerHTML = htmlErrore(e);
    contenitore.removeAttribute("aria-busy");
  }, "Carico i consigli", contenitore);
}

// --- memoria della pagina: si vede subito l'ultima versione, poi si aggiorna solo se è cambiata ----
const FRESCA = 10 * 60 * 1000;  // per 10 minuti non si chiede niente

function leggiMemoria(chiave) {
  try { return JSON.parse(localStorage.getItem("cerca:" + chiave) || "null"); } catch (e) { return null; }
}

function scriviMemoria(chiave, dati) {
  try { localStorage.setItem("cerca:" + chiave, JSON.stringify({ quando: Date.now(), dati })); } catch (e) { /* memoria piena */ }
}

async function conMemoria(chiave, percorso, disegna, errore, attesa, contenitore) {
  const salvata = leggiMemoria(chiave);
  if (salvata && salvata.dati) disegna(salvata.dati);
  else if (contenitore) contenitore.innerHTML = `<p class="caricamento">${esc(attesa)}</p>`;
  if (salvata && Date.now() - salvata.quando < FRESCA) return;
  try {
    const d = await chiama(percorso);
    const nuova = JSON.stringify(d);
    if (!salvata || JSON.stringify(salvata.dati) !== nuova) disegna(d);
    if (!d.errore) scriviMemoria(chiave, d);
  } catch (e) {
    if (!salvata) errore(e);  // con la versione salvata sulla pagina, un errore di rete non si vede
  }
}

// --- riquadro in evidenza, etichette, piattaforme ---------------------------------------------------
const NOMI_GENERI = {
  28: "Azione", 12: "Avventura", 16: "Animazione", 35: "Commedia", 80: "Crime", 99: "Documentario", 18: "Dramma",
  10751: "Famiglia", 14: "Fantasy", 36: "Storia", 27: "Horror", 10402: "Musica", 9648: "Mistero", 10749: "Romantico",
  878: "Fantascienza", 53: "Thriller", 10752: "Guerra", 37: "Western",
};

function htmlEtichette(f) {
  return f.nuovo ? '<span class="nastro">Aggiunto di recente</span>' : "";
}

function grande(url, misura) {
  return (url || "").replace("/s332/", `/${misura}/`).replace("/w342/", "/w780/");
}

// La vetrina in cima: con l'immagine di sfondo è una scena piena (piattaforme), altrimenti la locandina
// a destra e il suo colore sfumato dietro. Dopo qualche secondo parte il trailer muto.
function htmlInEvidenza(f, mondo, dove) {
  if (!f || !f.poster) return "";
  const generi = [f.serie ? "Serie" : "Film", ...(f.genre_ids || []).map((g) => NOMI_GENERI[g]).filter(Boolean).slice(0, 2)];
  const meta = [f.anno, generi.join(", "), votoBreve(f.voto)].filter(Boolean).join(" · ");
  const sonarr = `/sonarr/add/new?term=${encodeURIComponent(f.titolo_originale || f.titolo)}`;
  const trailer = chiaveTrailer(f);
  const azioni = (f.serie
    ? `<a class="bottone lampada" href="${sonarr}"${attrSerie(f)}>${ICONE.scarica}Segui la serie</a>`
    : `<a class="bottone lampada" href="${BASE}/film/${f.id}">${ICONE.scarica}Scarica</a>` +
      `<a class="bottone secondario" href="${BASE}/film/${f.id}">Scheda</a>`) +
    (trailer ? `<button type="button" class="secondario" data-apri-trailer="${esc(trailer)}">${ICONE.play}Trailer</button>` : "");
  const top = f.top10 ? `<p class="vetrina-top"><span class="num">N° ${f.top10}</span>oggi ${dove ? "su " + esc(dove) : (f.serie ? "tra le serie" : "tra i film")}</p>` : "";
  const testo = `<div class="vetrina-testo">${top}<h2><span class="rivela"><span>${esc(f.titolo)}</span></span></h2>` +
    `<p class="vetrina-generi">${esc(meta)}</p>${f.trama ? `<p class="vetrina-trama">${esc(f.trama)}</p>` : ""}` +
    `<div class="vetrina-azioni">${azioni}</div></div>`;
  const audio = trailer ? `<button type="button" class="audio-trailer" aria-pressed="false" aria-label="Ascolta l'audio del trailer" hidden>${ICONE.audioSpento}${ICONE.audioAcceso}</button>` : "";
  const dati = trailer ? ` data-trailer="${esc(trailer)}"` : "";
  if (f.sfondo) {
    return `<section class="vetrina scena"${dati} aria-label="In evidenza: ${esc(f.titolo)}">` +
      `<div class="sfondo"><img src="${esc(f.sfondo)}" alt=""></div><div class="vetrina-video"></div>${testo}${audio}</section>`;
  }
  const locandina = esc(grande(f.poster, "s718"));
  return `<section class="vetrina"${dati} aria-label="In evidenza: ${esc(f.titolo)}" style="--sfondo-vetrina:url('${locandina}')">` +
    `<div class="alone"></div>${testo}<div class="vetrina-locandina"><img src="${locandina}" alt=""><div class="vetrina-video"></div>${audio}</div></section>`;
}

function entraVetrina(contenitore) {
  const v = contenitore.querySelector(".vetrina");
  if (!v || v._entrata) return;
  v._entrata = true;
  if (!FERMO) {
    const t = v.querySelector(".rivela > span");
    if (t) G.from(t, { yPercent: 105, duration: 0.6, ease: "power3.out" });
    G.from(v.querySelectorAll(".vetrina-top, .vetrina-generi, .vetrina-trama, .vetrina-azioni"),
      { opacity: 0, y: 8, duration: 0.4, stagger: 0.06, delay: 0.2, ease: "power3.out", clearProps: "all" });
    const l = v.querySelector(".vetrina-locandina");
    if (l) G.from(l, { opacity: 0, y: 16, duration: 0.7, ease: "power3.out", clearProps: "opacity,transform" });
  }
  trailerVetrina(v);
}

async function avviaChipPiattaforme(mondo) {
  const contenitore = document.getElementById("righe");
  if (!contenitore || document.getElementById("chip-piattaforme")) return;
  const nav = document.createElement("nav");
  nav.id = "chip-piattaforme";
  nav.className = "chip-piattaforme";
  nav.setAttribute("aria-label", "Piattaforme");
  // subito sotto ricerca e generi: si vede senza scorrere
  const generi = document.getElementById("generi");
  if (generi) generi.after(nav); else contenitore.before(nav);
  // un tasto per piattaforma col suo logo vero; aprendola, Cerca prende i suoi colori
  await conMemoria(`piattaforme:${mondo}`, `/api/piattaforme?mondo=${encodeURIComponent(mondo)}`, (d) => {
    nav.innerHTML = d.tutte.map((p) =>
      `<a class="chip" href="${BASE}/piattaforma/${p.id}?mondo=${encodeURIComponent(mondo)}"${p.codice ? ` data-tema="${esc(p.codice)}"` : ""}>` +
      `${htmlLogo(p)}<span>${esc(p.nome)}</span></a>`).join("");
  }, () => nav.remove(), "", null);
}

// La "home" di una piattaforma: in evidenza, Top 10, novità e una riga per genere
async function avviaVista(id, mondo) {
  const contenitore = document.getElementById("vista");
  const menu = document.getElementById("menu-categorie");
  const disegna = (d) => {
    const nome = d.piattaforma.nome;
    contenitore.innerHTML = htmlInEvidenza(d.in_evidenza, mondo, nome) +
      (d.righe.length ? d.righe.map((r, i) => htmlRiga({ ...r, ancora: `riga-${i}` }, mondo)).join("")
        : `<p>Nessun titolo trovato su ${esc(nome)}.</p>`);
    menu.innerHTML = d.righe.map((r, i) => `<a href="#riga-${i}">${esc(r.titolo)}</a>`).join("");
    contenitore.removeAttribute("aria-busy");
    entraVetrina(contenitore);
  };
  await conMemoria(`vista:${id}:${mondo}`, `/api/vista/${id}?mondo=${encodeURIComponent(mondo)}`, disegna, (e) => {
    contenitore.innerHTML = htmlErrore(e);
    contenitore.removeAttribute("aria-busy");
  }, "Carico", contenitore);
  menu.addEventListener("click", (ev) => { if (ev.target.closest("a")) ev.target.closest("details").open = false; });
}

// Pagine di un genere o di una piattaforma: griglia con "Mostra altri"
function avviaGriglia(percorso) {
  const griglia = document.getElementById("griglia-genere");
  const altri = document.getElementById("altri");
  let pagina = 0;
  const visti = new Set();
  async function carica() {
    pagina += 1;
    altri.disabled = true;
    try {
      const d = await chiama(`${percorso}&pagina=${pagina}`);
      if (pagina === 1) griglia.innerHTML = "";
      const nuovi = d.elementi.filter((f) => !visti.has(f.id));
      nuovi.forEach((f) => visti.add(f.id));
      griglia.insertAdjacentHTML("beforeend", nuovi.map(htmlScheda).join(""));
      if (pagina === 1 && !nuovi.length) griglia.innerHTML = "<p>Nessun titolo trovato.</p>";
      altri.hidden = !d.altri;
    } catch (e) {
      griglia.insertAdjacentHTML("beforeend", htmlErrore(e));
      altri.hidden = true;
    }
    altri.disabled = false;
  }
  altri.addEventListener("click", carica);
  carica();
}

function avviaGenere(genere, mondo) {
  avviaGriglia(`/api/genere/${genere}?mondo=${encodeURIComponent(mondo)}`);
}

// Scelta delle piattaforme che hai (Netflix, Prime Video…): una riga ciascuna nella home
async function apriScegliPiattaforme(mondo) {
  const dialogo = document.getElementById("dialogo-piattaforme");
  const elenco = document.getElementById("elenco-piattaforme");
  elenco.innerHTML = '<p class="caricamento">Carico</p>';
  dialogo.showModal();
  try {
    const d = await chiama(`/api/piattaforme?mondo=${encodeURIComponent(mondo)}`);
    const scelte = new Set(d.scelte);
    elenco.innerHTML = d.tutte.map((p) =>
      `<label class="piattaforma-scelta"><input type="checkbox" value="${p.id}"${scelte.has(p.id) ? " checked" : ""}>` +
      htmlLogo(p) + `<span>${esc(p.nome)}</span></label>`).join("");
    document.getElementById("massimo-piattaforme").textContent = `Al massimo ${d.massimo}.`;
    elenco.onchange = () => {
      const attive = elenco.querySelectorAll("input:checked");
      elenco.querySelectorAll("input:not(:checked)").forEach((c) => { c.disabled = attive.length >= d.massimo; });
    };
    elenco.onchange();
  } catch (e) { elenco.innerHTML = htmlErrore(e); }
}

async function salvaPiattaforme(mondo) {
  const scelte = [...document.querySelectorAll("#elenco-piattaforme input:checked")].map((c) => Number(c.value));
  try {
    const r = await inviaJSON("/api/piattaforme", { scelte });
    document.getElementById("dialogo-piattaforme").close();
    avviso(r.messaggio);
    avviaHome(mondo);
  } catch (e) { avviso(e.errore || "Non riesco a salvare."); }
}

// --- Controllo servizi ---------------------------------------------------------------------
async function avviaDiagnosi() {
  const elenco = document.getElementById("elenco-diagnosi");
  const riassunto = document.getElementById("riassunto-diagnosi");
  const bottone = document.getElementById("ricontrolla");
  async function controlla() {
    bottone.disabled = true;
    elenco.innerHTML = '<li class="caricamento">Controllo i servizi (al massimo 10 secondi)</li>';
    riassunto.textContent = "";
    try {
      const d = await chiama("/api/diagnosi", { attesa: 30000 });
      // ogni servizio: il suo stato (animato) e le barre degli ultimi controlli fatti da questo browser
      const icona = { ok: "pronto", lento: "lento", errore: "errore" };
      elenco.innerHTML = d.risultati.map((r) =>
        `<li class="servizio-stato ${esc(r.stato)}"><span class="icona-stato">${iconaAnimata(icona[r.stato] || "problema")}</span>` +
        `<div><strong>${esc(r.nome)}</strong> <span class="sottotitolo">serve per ${esc(r.a_cosa_serve)}</span>` +
        `<p>${esc(r.messaggio)}</p>` +
        (r.cosa_fare ? `<p class="cosa-fare">${esc(r.cosa_fare)}</p>` : "") +
        htmlStorico(storicoServizio(r.nome, r.stato)) + "</div>" +
        `<span class="tempo">${r.millisecondi ? `${(r.millisecondi / 1000).toFixed(1).replace(".", ",")} s` : ""}</span></li>`).join("");
      const male = d.risultati.filter((r) => r.stato === "errore").map((r) => r.nome);
      riassunto.textContent = d.tutto_ok ? "Tutto a posto: tutti i servizi rispondono"
        : male.length ? `Non rispondono: ${male.join(", ")}` : "Rispondono tutti, ma qualcuno è lento";
      const b = d.ultimo_blocco;
      if (b) {
        const quando = new Date(b.quando).toLocaleString("it-IT", { dateStyle: "short", timeStyle: "short" });
        elenco.insertAdjacentHTML("beforeend",
          `<li class="servizio-stato lento"><span class="icona-stato">${iconaAnimata("lento")}</span><div>` +
          `<strong>Ultimo blocco di Cerca</strong> <span class="sottotitolo">${esc(quando)}</span>` +
          `<p>Era fermo da ${b.secondi} secondi${b.riavviato ? " e si è riavviato da solo" : ""}.</p>` +
          (b.punti && b.punti.length ? `<details><summary>Dove era fermo (da mandare a chi ti aiuta)</summary><pre class="dove-blocco">${esc(b.punti.join("\n"))}</pre></details>` : "") +
          '</div><span class="tempo"></span></li>');
      }
    } catch (e) {
      elenco.innerHTML = `<li>${htmlErrore(e)}</li>`;
    }
    bottone.disabled = false;
  }
  bottone.addEventListener("click", controlla);
  controlla();
}

// --- Musica: home, artista, album, ricerca e mini-lettore delle anteprime da 30 secondi ----------
function durataBrano(s) {
  const m = Math.floor((s || 0) / 60);
  return `${m}:${String((s || 0) % 60).padStart(2, "0")}`;
}

const PLAY = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5v13l11-6.5z"/></svg>';
const PAUSA = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z"/></svg>';

function htmlCartaArtista(a) {
  return `<a class="carta-musica carta-artista" href="${BASE}/musica/artista/${a.id}">` +
    `<span class="immagine tonda">${a.foto ? `<img src="${esc(a.foto)}" alt="" loading="lazy">` : ""}</span>` +
    `<span class="nome">${esc(a.nome)}</span><span class="dettaglio">Artista</span></a>`;
}

function htmlCartaAlbum(a) {
  const sotto = [a.anno, a.artista].filter(Boolean).join(" • ");
  return `<a class="carta-musica" href="${BASE}/musica/album/${a.id}">` +
    `<span class="immagine">${a.copertina ? `<img src="${esc(a.copertina)}" alt="" loading="lazy">` : ""}` +
    `<span class="play-tondo" aria-hidden="true">${PLAY}</span></span>` +
    `<span class="nome">${esc(a.titolo)}</span><span class="dettaglio">${esc(sotto)}</span></a>`;
}

function htmlCartaBrano(t, coda) {
  return `<button type="button" class="carta-musica" data-brano="${t.id}" data-coda="${coda}">` +
    `<span class="immagine">${t.copertina ? `<img src="${esc(t.copertina)}" alt="" loading="lazy">` : ""}` +
    `<span class="play-tondo" aria-hidden="true">${PLAY}</span></span>` +
    `<span class="nome">${esc(t.titolo)}</span><span class="dettaglio">${esc(t.artista)}</span></button>`;
}

const SCARICA = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="10.5" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M12 6.5v8.2m0 0-3.4-3.4m3.4 3.4 3.4-3.4M7.5 17.5h9" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const SCARICATO = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="11" fill="currentColor"/><path d="m7.5 12.3 3 3 6-6.3" fill="none" stroke="#121212" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';

function htmlRigaBrano(t, i, coda, conCopertina = true) {
  return `<div class="riga-con-scarica"><button type="button" class="riga-brano" data-brano="${t.id}" data-coda="${coda}">` +
    `<span class="numero">${t.numero || i + 1}</span>` +
    (conCopertina ? `<img src="${esc(t.copertina)}" alt="" loading="lazy">` : "") +
    `<span class="testi"><span class="titolo">${esc(t.titolo)}</span><span class="artista">${esc(t.artista)}</span></span>` +
    `<span class="durata">${durataBrano(t.durata)}</span></button>` +
    `<button type="button" class="scarica-musica" data-scarica="brano" data-id="${t.id}" ` +
    `aria-label="Scarica la canzone completa: ${esc(t.titolo)}" title="Scarica la canzone completa">${SCARICA}</button></div>`;
}

// Scarica la canzone completa (o l'album): Cerca la cerca sugli indexer e la mette nella Musica di Jellyfin
async function scaricaMusica(b) {
  if (b.classList.contains("fatto") || b.disabled) return;
  b.disabled = true;
  b.classList.add("in-corso");
  try {
    avviso((await inviaJSON("/api/musica/scarica", { tipo: b.dataset.scarica, id: Number(b.dataset.id) })).messaggio);
    b.classList.add("fatto");
    if (b.dataset.scarica === "brano") b.innerHTML = SCARICATO;
    else b.innerHTML = `${SCARICATO}<span>In download</span>`;
  } catch (e) {
    avviso(`${e.errore} ${e.cosa_fare || ""}`, true);
    b.disabled = false;
  }
  b.classList.remove("in-corso");
}

document.addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-scarica]");
  if (b) { ev.preventDefault(); scaricaMusica(b); }
});

// Brani preferiti: quelli scaricati, nella Musica di Jellyfin. Si ascoltano interi.
const CUORE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20.5s-7.5-4.6-9.3-9.2C1.4 8 3.4 4.5 7 4.5c2 0 3.6 1.1 5 3 1.4-1.9 3-3 5-3 3.6 0 5.6 3.5 4.3 6.8-1.8 4.6-9.3 9.2-9.3 9.2z" fill="currentColor"/></svg>';

function copertinaJf(t) {
  return t.copertina_jf ? `${BASE}/api/musica/copertina/${t.copertina_jf}` : "";
}

function htmlRigaPreferito(t, i, coda) {
  const copertina = copertinaJf(t);
  return `<div class="riga-con-scarica"><button type="button" class="riga-brano" data-brano="jf:${esc(t.id)}" data-coda="${coda}" ` +
    `data-titolo="${esc(t.titolo)}" data-artista="${esc(t.artista)}" data-copertina="${esc(copertina)}">` +
    `<span class="numero">${i + 1}</span>` +
    (copertina ? `<img src="${esc(copertina)}" alt="" loading="lazy">` : `<span class="copertina-vuota" aria-hidden="true">${ICONE.nota}</span>`) +
    `<span class="testi"><span class="titolo">${esc(t.titolo)}</span><span class="artista">${esc(t.artista)}</span></span>` +
    `<span class="durata">${durataBrano(t.durata)}</span></button>` +
    `<span class="scarica-musica fatto" title="Scaricato" aria-label="Scaricato">${SCARICATO}</span></div>`;
}

function codaPreferiti(brani) {
  return brani.map((t) => `jf:${t.id}`).join(",");
}

function htmlRigaMusica(r) {
  if (r.tipo === "preferiti") {
    const coda = codaPreferiti(r.elementi);
    return `<section class="riga-musica"><h2><a href="${BASE}/musica/preferiti">${esc(r.titolo)}</a></h2>` +
      `<p class="sottotitolo">${esc(r.sottotitolo)}</p><div class="elenco-brani">` +
      r.elementi.map((t, i) => htmlRigaPreferito(t, i, coda)).join("") + `</div>` +
      (r.quanti > r.elementi.length ? `<a class="vedi-tutti" href="${BASE}/musica/preferiti">Vedi tutti (${r.quanti})</a>` : "") +
      `</section>`;
  }
  const coda = r.tipo === "brani" ? r.elementi.map((t) => t.id).join(",") : "";
  const carte = r.elementi.map((x) =>
    r.tipo === "artisti" ? htmlCartaArtista(x) : r.tipo === "album" ? htmlCartaAlbum(x) : htmlCartaBrano(x, coda)).join("");
  return `<section class="riga-musica"><h2>${esc(r.titolo)}</h2>` +
    (r.sottotitolo ? `<p class="sottotitolo">${esc(r.sottotitolo)}</p>` : "") +
    `<div class="striscia-musica">${carte}</div></section>`;
}

// Il mini-lettore in basso: si apre al primo brano e passa da solo al successivo
const lettore = {
  audio: null, coda: [], posto: 0,
  elemento() {
    let el = document.getElementById("lettore");
    if (el) return el;
    document.body.insertAdjacentHTML("beforeend",
      '<div id="lettore" class="lettore" hidden><img id="lettore-copertina" alt="">' +
      '<span class="testi"><span id="lettore-titolo" class="titolo"></span><span id="lettore-artista" class="artista"></span></span>' +
      `<button type="button" id="lettore-play" class="lettore-play" aria-label="Pausa">${PAUSA}</button>` +
      '<span class="lettore-barra"><span id="lettore-avanzamento"></span></span></div>');
    el = document.getElementById("lettore");
    this.audio = new Audio();
    this.audio.addEventListener("timeupdate", () => {
      const a = this.audio;
      document.getElementById("lettore-avanzamento").style.width = a.duration ? `${100 * a.currentTime / a.duration}%` : "0";
    });
    this.audio.addEventListener("ended", () => this.prossimo());
    this.audio.addEventListener("play", () => this.icona());
    this.audio.addEventListener("pause", () => this.icona());
    document.getElementById("lettore-play").addEventListener("click", () => {
      if (this.audio.paused) this.audio.play(); else this.audio.pause();
    });
    return el;
  },
  icona() {
    const b = document.getElementById("lettore-play");
    b.innerHTML = this.audio.paused ? PLAY : PAUSA;
    b.setAttribute("aria-label", this.audio.paused ? "Riproduci" : "Pausa");
    document.querySelectorAll("[data-brano]").forEach((x) => x.classList.toggle("in-ascolto",
      !this.audio.paused && x.dataset.brano === String(this.coda[this.posto])));
  },
  // "jf:<id>" = brano scaricato, intero (da Jellyfin); un numero = anteprima da 30 s (Deezer)
  async info(id) {
    if (String(id).startsWith("jf:")) {
      const riga = document.querySelector(`[data-brano="${CSS.escape(id)}"][data-titolo]`);
      const d = riga ? riga.dataset : {};
      return { titolo: d.titolo || "", artista: d.artista || "", copertina: d.copertina || "",
        url: `${BASE}/api/musica/ascolta/${id.slice(3)}`, intero: true };
    }
    return chiama(`/api/musica/anteprima/${id}`);
  },
  async suona(id, coda) {
    const el = this.elemento();
    this.coda = coda && coda.length ? coda : [id];
    this.posto = Math.max(0, this.coda.indexOf(id));
    try {
      const t = await this.info(id);
      document.getElementById("lettore-copertina").src = t.copertina || "";
      document.getElementById("lettore-titolo").textContent = t.titolo;
      document.getElementById("lettore-artista").textContent = t.intero ? t.artista : `${t.artista} · anteprima`;
      el.hidden = false;
      document.body.classList.add("con-lettore");
      this.audio.src = t.url;
      if ("mediaSession" in navigator) {  // titolo e copertina anche sulla schermata di blocco
        navigator.mediaSession.metadata = new MediaMetadata({ title: t.titolo, artist: t.artista,
          artwork: t.copertina ? [{ src: t.copertina, sizes: "400x400" }] : [] });
        navigator.mediaSession.setActionHandler("nexttrack", () => this.prossimo());
        navigator.mediaSession.setActionHandler("previoustrack", () => this.precedente());
      }
      await this.audio.play();
    } catch (e) {
      avviso(`${e.errore || "Non riesco a riprodurre questo brano."} ${e.cosa_fare || ""}`, true);
    }
  },
  prossimo() {
    if (this.posto + 1 < this.coda.length) this.suona(this.coda[this.posto + 1], this.coda);
  },
  precedente() {
    if (this.audio.currentTime > 3 || this.posto === 0) this.audio.currentTime = 0;
    else this.suona(this.coda[this.posto - 1], this.coda);
  },
};

document.addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-brano]");
  if (!b) return;
  ev.preventDefault();
  const id = (x) => (/^\d+$/.test(x) ? Number(x) : x);
  const coda = (b.dataset.coda || "").split(",").filter(Boolean).map(id);
  lettore.suona(id(b.dataset.brano), coda);
});

async function seguiArtista(b) {
  b.disabled = true;
  try {
    avviso((await inviaJSON("/api/musica/segui", { nome: b.dataset.nome })).messaggio);
    b.textContent = "Lo segui";
    b.classList.add("seguito");
  } catch (e) { avviso(`${e.errore} ${e.cosa_fare || ""}`, true); b.disabled = false; }
}

function avviaMusica() {
  const scorciatoie = document.getElementById("scorciatoie");
  const righe = document.getElementById("righe-musica");
  conMemoria("musica:home", "/api/musica/home", (d) => {
    const preferiti = d.preferiti
      ? `<a class="scorciatoia scorciatoia-preferiti" href="${BASE}/musica/preferiti"><span class="cuore">${CUORE}</span><span>Brani preferiti</span></a>` : "";
    scorciatoie.innerHTML = preferiti + d.scorciatoie.slice(0, preferiti ? 7 : 8).map((a) =>
      `<a class="scorciatoia" href="${BASE}/musica/artista/${a.id}">` +
      `${a.foto ? `<img src="${esc(a.foto)}" alt="">` : "<span></span>"}<span>${esc(a.nome)}</span></a>`).join("");
    righe.innerHTML = d.righe.map(htmlRigaMusica).join("") +
      (d.avviso && !d.righe.length ? `<div class="nota"><p>${esc(d.avviso)}</p></div>` : "");
    righe.removeAttribute("aria-busy");
  }, (e) => { righe.innerHTML = htmlErrore(e); righe.removeAttribute("aria-busy"); }, "Carico la tua musica", righe);
}

async function avviaArtista(id) {
  const pagina = document.getElementById("pagina-artista");
  try {
    const a = await chiama(`/api/musica/artista/${id}`);
    document.title = `${a.nome} · mediabox`;
    const coda = a.popolari.map((t) => t.id).join(",");
    const fan = a.fan ? `${Number(a.fan).toLocaleString("it-IT")} fan` : "";
    pagina.innerHTML =
      `<header class="testa-musica testa-artista" style="--sfondo-artista:url('${esc(a.foto_grande)}')">` +
      `<div class="testa-testo"><p class="etichetta-musica">Artista</p><h1>${esc(a.nome)}</h1><p class="dettaglio">${esc(fan)}</p></div></header>` +
      `<div class="azioni-musica">` +
      (a.popolari.length ? `<button type="button" class="play-grande" data-brano="${a.popolari[0].id}" data-coda="${coda}" aria-label="Ascolta i brani popolari">${PLAY}</button>` : "") +
      (window.LIDARR ? `<button type="button" class="segui" data-nome="${esc(a.nome)}">Segui</button>` : "") + `</div>` +
      (a.popolari.length ? `<section class="riga-musica"><h2>Popolari</h2><div class="elenco-brani">` +
        a.popolari.map((t, i) => htmlRigaBrano(t, i, coda)).join("") + `</div></section>` : "") +
      (a.album.length ? htmlRigaMusica({ tipo: "album", titolo: "Album", elementi: a.album }) : "") +
      (a.singoli.length ? htmlRigaMusica({ tipo: "album", titolo: "Singoli ed EP", elementi: a.singoli }) : "") +
      (a.simili.length ? htmlRigaMusica({ tipo: "artisti", titolo: "Ti potrebbe piacere", elementi: a.simili }) : "");
    const b = pagina.querySelector("button.segui");
    if (b) b.addEventListener("click", () => seguiArtista(b));
  } catch (e) { pagina.innerHTML = htmlErrore(e); }
  pagina.removeAttribute("aria-busy");
}

async function avviaAlbum(id) {
  const pagina = document.getElementById("pagina-album");
  try {
    const a = await chiama(`/api/musica/album/${id}`);
    document.title = `${a.titolo} · mediabox`;
    const coda = a.brani.map((t) => t.id).join(",");
    const minuti = Math.round((a.durata || 0) / 60);
    const sotto = [a.anno, `${a.brani.length} brani`, minuti ? `${minuti} min` : ""].filter(Boolean).join(" • ");
    pagina.innerHTML =
      `<header class="testa-musica testa-album"><img src="${esc(a.copertina_grande || a.copertina)}" alt="">` +
      `<div class="testa-testo"><p class="etichetta-musica">${a.tipo === "album" ? "Album" : "Singolo"}</p><h1>${esc(a.titolo)}</h1>` +
      `<p class="dettaglio"><a href="${BASE}/musica/artista/${a.artista_id}">${esc(a.artista)}</a> • ${esc(sotto)}</p></div></header>` +
      `<div class="azioni-musica">` +
      (a.brani.length ? `<button type="button" class="play-grande" data-brano="${a.brani[0].id}" data-coda="${coda}" aria-label="Ascolta l'album">${PLAY}</button>` : "") +
      `<button type="button" class="scarica-musica scarica-album" data-scarica="album" data-id="${a.id}">${SCARICA}<span>${a.tipo === "album" ? "Scarica l'album" : "Scarica"}</span></button>` +
      (window.LIDARR ? `<button type="button" class="segui" data-nome="${esc(a.artista)}">Segui l'artista</button>` : "") + `</div>` +
      `<div class="elenco-brani">${a.brani.map((t, i) => htmlRigaBrano(t, i, coda, false)).join("")}</div>` +
      (a.etichetta ? `<p class="sottotitolo piccolo">${esc(a.etichetta)}</p>` : "");
    const b = pagina.querySelector("button.segui");
    if (b) b.addEventListener("click", () => seguiArtista(b));
  } catch (e) { pagina.innerHTML = htmlErrore(e); }
  pagina.removeAttribute("aria-busy");
}

async function avviaPreferiti() {
  const pagina = document.getElementById("pagina-preferiti");
  try {
    const { brani } = await chiama("/api/musica/preferiti");
    const coda = codaPreferiti(brani);
    const minuti = Math.round(brani.reduce((n, t) => n + (t.durata || 0), 0) / 60);
    const sotto = [`${brani.length} ${brani.length === 1 ? "brano" : "brani"}`, minuti ? `${minuti} min` : ""].filter(Boolean).join(" • ");
    pagina.innerHTML =
      `<header class="testa-musica testa-album testa-preferiti"><span class="copertina-preferiti">${CUORE}</span>` +
      `<div class="testa-testo"><p class="etichetta-musica">Playlist</p><h1>Brani preferiti</h1><p class="dettaglio">${esc(sotto)}</p></div></header>` +
      (brani.length
        ? `<div class="azioni-musica"><button type="button" class="play-grande" data-brano="jf:${esc(brani[0].id)}" data-coda="${coda}" ` +
          `aria-label="Ascolta i brani preferiti">${PLAY}</button></div>` +
          `<div class="elenco-brani">${brani.map((t, i) => htmlRigaPreferito(t, i, coda)).join("")}</div>`
        : `<div class="nota"><p>Ancora nessun brano. Cerca una canzone e tocca l'icona di download: quando è scaricata compare qui e la ascolti intera.</p></div>`);
  } catch (e) { pagina.innerHTML = htmlErrore(e); }
  pagina.removeAttribute("aria-busy");
}

async function avviaCercaMusica(q) {
  const box = document.getElementById("risultati-musica");
  if (!q) { box.innerHTML = ""; return; }
  box.innerHTML = '<p class="caricamento">Cerco</p>';
  try {
    const d = await chiama(`/api/musica/cerca?q=${encodeURIComponent(q)}`);
    const coda = d.brani.map((t) => t.id).join(",");
    box.innerHTML =
      (d.brani.length ? `<section class="riga-musica"><h2>Brani</h2><div class="elenco-brani">` +
        d.brani.map((t, i) => htmlRigaBrano(t, i, coda)).join("") + `</div></section>` : "") +
      (d.artisti.length ? htmlRigaMusica({ tipo: "artisti", titolo: "Artisti", elementi: d.artisti }) : "") +
      (d.album.length ? htmlRigaMusica({ tipo: "album", titolo: "Album", elementi: d.album }) : "") ||
      `<p>Nessun risultato per "${esc(q)}"</p>`;
  } catch (e) { box.innerHTML = htmlErrore(e); }
}

// --- pagina Spazio ----------------------------------------------------------------
function avviaSpazio() {
  const anteprima = document.getElementById("anteprima");
  const registro = document.getElementById("registro");
  const bottone = document.getElementById("libera");
  const totale = document.getElementById("totale-liberabile");

  // la lancetta del disco (bklit-ui: gauge), con la zona della pulizia automatica in rosso
  const disco = document.querySelector(".disco[data-usato]");
  if (disco) {
    const box = document.getElementById("lancetta");
    box.innerHTML = htmlLancetta(Number(disco.dataset.usato), Number(disco.dataset.soglia));
    muoviLancetta(box.querySelector("svg"));
  }

  function voce(testo, byte, dettaglio) {
    return `<li><div class="descrizione"><strong>${esc(testo)}</strong>` +
      `<span class="sottotitolo">${esc(dettaglio)}</span></div><div class="azioni num">${dimensione(byte)}</div></li>`;
  }

  async function carica() {
    try {
      const d = await chiama("/api/pulizia");
      let html = "";
      if (d.abbandonati.length) {
        html += "<h3>Download abbandonati, tolti sempre</h3><ul class=\"risultati\">" +
          d.abbandonati.map((a) => voce(a.nome, a.dimensione, `fermo da ${a.giorni} giorni, non lo usa nessun download`)).join("") + "</ul>";
      }
      if (d.pulizia_visti) {
        html += `<h3>Già visti da più di ${d.giorni} giorni, tolti solo se serve spazio, dal più vecchio</h3>`;
        html += d.visti.length
          ? "<ul class=\"risultati\">" + d.visti.map((v) => voce(v.serie ? `${v.serie} – ${v.nome}` : v.nome, v.dimensione,
              `visto ${v.giorni} giorni fa`)).join("") + "</ul>"
          : "<p class=\"sottotitolo\">Nessun film o episodio visto da abbastanza tempo</p>";
      } else {
        html += '<div class="nota"><p>La pulizia dei film e degli episodi già visti è spenta. Puoi attivarla con ' +
          '<code>mediabox impostazioni</code>: tocca solo quello che è già stato visto da tempo, mai i preferiti.</p></div>';
      }
      if (!d.abbandonati.length && !(d.pulizia_visti && d.visti.length)) {
        html = `<p class="stato" style="color:var(--verde)">${iconaAnimata("pronto")}Niente da togliere, tutto in ordine</p>` + html;
      }
      anteprima.innerHTML = html;
      totale.innerHTML = d.totale ? `Totale <span class="num" style="color:var(--testo)">${dimensione(d.totale)}</span>` : "";
      bottone.disabled = !d.totale;
      bottone.lastChild.textContent = d.totale ? `Libera ${dimensione(d.totale)}` : "Libera spazio adesso";
      registro.innerHTML = d.registro.length
        ? "<ul class=\"risultati\">" + d.registro.map((r) => voce(r.cosa, r.dimensione,
            new Date(r.quando * 1000).toLocaleString("it-IT", { dateStyle: "medium", timeStyle: "short" }) + " · " + (r.motivo || ""))).join("") + "</ul>"
        : "<p class=\"sottotitolo\">Nessuna pulizia finora</p>";
    } catch (e) { anteprima.innerHTML = htmlErrore(e); registro.innerHTML = ""; }
  }

  // "Libera" si conferma tenendo premuto: toglie i download abbandonati e, se serve spazio e la
  // pulizia dei visti è attiva, i film ed episodi visti da tempo
  tieniPremuto(bottone, async () => {
    bottone.disabled = true;
    try { await conAvviso(chiama("/api/pulizia", { method: "POST" }), "Faccio pulizia"); } catch (e) { /* già detto */ }
    bottone.classList.remove("confermato");
    bottone._presa = false;
    carica();
  });
  carica();
}

// --- "Segui la serie": la aggiunge a Sonarr senza passare dalla sua pagina (in inglese) ----------
function attrSerie(f) {
  const dati = {
    nome: f.titolo, titolo: f.titolo_originale || f.titolo, anno: f.anno || "", poster: f.poster || "",
    trama: f.trama || "", imdb: f.imdb_id || "", tvdb: f.tvdb || null,
    tmdb: f.generi_tvmaze ? null : f.id,  // le serie di TVmaze hanno un id loro, non quello di TMDB
  };
  return ` data-serie="${esc(JSON.stringify(dati))}"`;
}

const STATI_PUNTATA = {
  pronta: "In Jellyfin", download: "In download", cerco: "Cerco una fonte",
  in_arrivo: "La scarico appena esce", libera: "", non_uscita: "Non ancora uscita",
};
const ICONA_PUNTATA = { pronta: "pronto", download: "download", cerco: "cerco", in_arrivo: "in_arrivo" };

function htmlPuntate(stagioni) {
  if (!stagioni.length) return '<p class="sottotitolo">Sonarr non ha ancora l\'elenco delle puntate: riprova tra poco.</p>';
  return stagioni.map((st) => {
    const pronte = st.puntate.filter((p) => p.stato === "pronta").length;
    const seguite = st.puntate.some((p) => p.seguita && p.stato !== "pronta");
    const righe = st.puntate.map((p) => {
      let bottone = "";
      if (p.stato === "libera") bottone = `<button class="secondario" data-puntata="${p.id}" aria-label="Scarica la puntata ${p.numero}">${ICONE.scarica}</button>`;
      else if (["download", "cerco", "in_arrivo"].includes(p.stato)) bottone = `<button class="secondario" data-tieni data-annulla-puntata="${p.id}">Annulla</button>`;
      const stato = p.stato === "download" ? `In download ${Math.round(p.percentuale)}%` : STATI_PUNTATA[p.stato];
      return `<div class="puntata"><div class="descrizione"><span><strong>${p.numero}.</strong> ${esc(p.titolo || "Puntata " + p.numero)}</span>` +
        `<span class="stato-puntata">${iconaAnimata(ICONA_PUNTATA[p.stato] || "")}${esc(stato || "")}${p.data ? (stato ? " · " : "") + esc(p.data) : ""}</span></div>${bottone}</div>`;
    }).join("");
    const azione = seguite
      ? `<button class="secondario" data-tieni data-annulla-stagione="${st.numero}">Annulla stagione</button>`
      : (pronte < st.puntate.length ? `<button class="secondario" data-stagione="${st.numero}">${ICONE.scarica}Stagione</button>` : "");
    return `<details class="stagione"${stagioni.length === 1 ? " open" : ""}><summary><span>Stagione ${st.numero} ` +
      `<span class="sottotitolo">${pronte}/${st.puntate.length} in Jellyfin</span></span>${azione}</summary>${righe}</details>`;
  }).join("");
}

function dialogoSerie() {
  let d = document.getElementById("dialogo-serie");
  if (d) return d;
  document.body.insertAdjacentHTML("beforeend", `
<dialog id="dialogo-serie" aria-labelledby="titolo-serie">
  <div class="modulo">
    <div class="serie-testa"><img id="serie-poster" alt=""><div><h2 id="titolo-serie"></h2><p class="sottotitolo" id="serie-anno"></p></div></div>
    <p id="serie-trama" class="serie-trama"></p>
    <button type="button" id="serie-tutte" class="bottone lampada scarica-grande">${ICONE.scarica}Scarica tutte</button>
    <p class="sottotitolo">Tutte le puntate già uscite e quelle nuove appena escono. Oppure scegli qui sotto
      una stagione o una puntata. Prima la versione in italiano.</p>
    <p id="serie-esito" class="messaggio-serie" role="status"></p>
    <div id="serie-puntate" class="serie-puntate"></div>
    <div class="azioni-riga">
      <button type="button" class="secondario" id="serie-annulla-tutte" data-tieni hidden>Annulla tutte</button>
      <button type="button" class="secondario" id="serie-chiudi">Chiudi</button>
    </div>
  </div>
</dialog>`);
  d = document.getElementById("dialogo-serie");
  const box = document.getElementById("serie-puntate");
  const esito = document.getElementById("serie-esito");
  d.addEventListener("close", () => clearInterval(d._aggiorna));
  document.getElementById("serie-chiudi").addEventListener("click", () => d.close());

  // gli "Annulla" si confermano tenendo premuto: smetto di cercarle e fermo i download (quelle in Jellyfin restano)
  async function invia(percorso, corpo) {
    try {
      const r = await inviaJSON(`/api/serie/${d.dataset.serieId}/${percorso}`, corpo);
      esito.className = "messaggio-serie ok";
      esito.textContent = r.messaggio;
      await aggiornaPuntate(d);
    } catch (e) {
      esito.className = "messaggio-serie errore";
      esito.textContent = `${e.errore || "Non ci sono riuscito."} ${e.cosa_fare || ""}`;
    }
  }
  document.getElementById("serie-tutte").addEventListener("click", () => invia("scarica", { tutte: true }));
  const annullaTutte = document.getElementById("serie-annulla-tutte");
  tieniPremuto(annullaTutte, async () => {
    await invia("annulla", { tutte: true });
    annullaTutte.classList.remove("confermato");
    annullaTutte._presa = false;
  });
  d._azione = (b) => {
    b.disabled = true;
    if (b.dataset.puntata) invia("scarica", { puntate: [Number(b.dataset.puntata)] });
    else if (b.dataset.annullaPuntata) invia("annulla", { puntate: [Number(b.dataset.annullaPuntata)] });
    else if (b.dataset.stagione) invia("scarica", { stagione: Number(b.dataset.stagione) });
    else if (b.dataset.annullaStagione) invia("annulla", { stagione: Number(b.dataset.annullaStagione) });
  };
  box.addEventListener("click", (ev) => {
    const b = ev.target.closest("button");
    if (!b) return;
    ev.preventDefault();  // il clic sul tasto della stagione non apre/chiude l'elenco
    if (!b.hasAttribute("data-tieni")) d._azione(b);
  });
  return d;
}

async function aggiornaPuntate(d) {
  if (!d.dataset.serieId) return;
  const box = document.getElementById("serie-puntate");
  try {
    const r = await chiama(`/api/serie/${d.dataset.serieId}/puntate`);
    const aperteNumeri = new Set([...box.querySelectorAll("details[open] summary")].map((x) => x.textContent.match(/Stagione (\d+)/)?.[1]));
    box.innerHTML = htmlPuntate(r.stagioni);
    preparaTieni(box, d._azione);
    box.querySelectorAll("details").forEach((x) => {
      if (aperteNumeri.has(x.querySelector("summary").textContent.match(/Stagione (\d+)/)?.[1])) x.open = true;
    });
    const tutte = r.stagioni.flatMap((st) => st.puntate);
    document.getElementById("serie-annulla-tutte").hidden = !tutte.some((p) => p.seguita && p.stato !== "pronta");
  } catch (e) { /* riprova al prossimo giro */ }
}

async function apriSerie(dati) {
  const d = dialogoSerie();
  d.dataset.serie = JSON.stringify(dati);
  delete d.dataset.serieId;
  const poster = document.getElementById("serie-poster");
  poster.hidden = !dati.poster;
  if (dati.poster) poster.src = dati.poster;
  document.getElementById("titolo-serie").textContent = dati.nome || dati.titolo;
  document.getElementById("serie-anno").textContent =
    [dati.anno, dati.titolo !== dati.nome ? dati.titolo : ""].filter(Boolean).join(" · ");
  document.getElementById("serie-trama").textContent = dati.trama || "";
  const esito = document.getElementById("serie-esito");
  esito.className = "messaggio-serie";
  esito.textContent = "";
  const box = document.getElementById("serie-puntate");
  box.innerHTML = '<p class="caricamento">Carico stagioni e puntate</p>';
  document.getElementById("serie-tutte").disabled = true;
  document.getElementById("serie-annulla-tutte").hidden = true;
  d.showModal();
  try {
    const r = await inviaJSON("/api/serie/apri", { titolo: dati.titolo, nome: dati.nome || "", imdb: dati.imdb, tvdb: dati.tvdb, tmdb: dati.tmdb });
    d.dataset.serieId = r.serie_id;
    box.innerHTML = htmlPuntate(r.stagioni);
    preparaTieni(box, d._azione);
    document.getElementById("serie-tutte").disabled = false;
    const tutte = r.stagioni.flatMap((st) => st.puntate);
    document.getElementById("serie-annulla-tutte").hidden = !tutte.some((p) => p.seguita && p.stato !== "pronta");
    clearInterval(d._aggiorna);
    d._aggiorna = setInterval(() => aggiornaPuntate(d), 8000);
  } catch (e) {
    box.innerHTML = "";
    esito.className = "messaggio-serie errore";
    esito.textContent = `${e.errore || "Non ci sono riuscito."} ${e.cosa_fare || ""}`;
  }
}

document.addEventListener("click", (ev) => {
  const a = ev.target.closest("a[data-serie]");
  if (!a || ev.ctrlKey || ev.metaKey || ev.shiftKey) return;  // con Ctrl si apre comunque Sonarr
  ev.preventDefault();
  try { apriSerie(JSON.parse(a.dataset.serie), a.href); } catch (e) { window.location.href = a.href; }
});
