// Cerca — script delle pagine. Niente librerie esterne, niente build.
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
  const box = document.getElementById("avviso");
  box.textContent = testo;
  box.classList.toggle("errore", errore);
  box.hidden = false;
  clearTimeout(avviso._t);
  avviso._t = setTimeout(() => { box.hidden = true; }, 6000);
}

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
    `<p><a href="${BASE}/diagnosi">Controlla quali servizi rispondono →</a></p></div>`;
}

// Segnaposto per i film senza locandina: titolo in alto, anno in basso
function htmlSenzaPoster(titolo, anno) {
  return `<span class="senza-poster"><span class="titolo">${esc(titolo)}</span><span class="anno">${esc(anno || "")}</span></span>`;
}

// --- pagina del film ------------------------------------------------------
function avviaFilm(tmdbId) {
  const sezione = (id) => document.querySelector(`#${id} .contenuto`);

  // Le ricerche partono insieme, ognuna riempie la sua sezione.
  chiama(`/api/film/${tmdbId}/provider`)
    .then((d) => { sezione("provider").innerHTML = htmlProvider(d); })
    .catch((e) => { sezione("provider").innerHTML = htmlErrore(e); });

  if (document.getElementById("archive")) {
    chiama(`/api/film/${tmdbId}/archive`)
      .then((d) => { sezione("archive").innerHTML = htmlArchive(d); })
      .catch((e) => { sezione("archive").innerHTML = htmlErrore(e); });

    const riassunto = document.getElementById("riassunto-prowlarr");
    chiama(`/api/film/${tmdbId}/prowlarr`, { attesa: 120000 })
      .then((d) => {
        sezione("prowlarr").innerHTML = htmlProwlarr(d);
        riassunto.textContent = d.avviso ? d.avviso
          : d.risultati.length === 1 ? "1 risultato" : `${d.risultati.length} risultati`;
      })
      .catch((e) => { sezione("prowlarr").innerHTML = htmlErrore(e); riassunto.textContent = e.errore || "Errore"; });

    document.getElementById("archive").addEventListener("click", async (ev) => {
      const b = ev.target.closest("button[data-identificativo]");
      if (!b) return;
      b.disabled = true;
      try {
        const r = await inviaJSON("/api/scarica/archive", {
          tmdb_id: tmdbId, identificativo: b.dataset.identificativo, nome: b.dataset.nome, modo: b.dataset.modo,
        });
        avviso(r.messaggio);
        b.textContent = "✓ Avviato";
      } catch (e) { avviso(e.errore + " " + (e.cosa_fare || ""), true); b.disabled = false; }
    });

    document.getElementById("prowlarr").addEventListener("click", async (ev) => {
      const b = ev.target.closest("button[data-risultato]");
      if (!b) return;
      b.disabled = true;
      try {
        const r = await inviaJSON("/api/scarica/prowlarr", { tmdb_id: tmdbId, id: b.dataset.risultato });
        avviso(r.messaggio);
        b.textContent = "✓ Avviato";
      } catch (e) { avviso(e.errore + " " + (e.cosa_fare || ""), true); b.disabled = false; }
    });
  }

  const avvisami = document.getElementById("avvisami");
  if (avvisami) avvisami.addEventListener("click", async () => {
    avvisami.disabled = true;
    try {
      const r = await chiama(`/api/avvisami/${tmdbId}`, { method: "POST" });
      avvisami.textContent = r.attivo ? "✓ Ti avviserò se diventa gratis" : "🔔 Avvisami se diventa disponibile gratis";
      avvisami.className = r.attivo ? "attivo" : "secondario";
      avviso(r.messaggio);
    } catch (e) { avviso(e.errore, true); }
    avvisami.disabled = false;
  });
}

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

function htmlArchive(d) {
  if (!d.versioni.length) {
    return d.item_trovati
      ? "<p>Ho trovato qualcosa su Internet Archive, ma nessun file sembra il film completo.</p>"
      : "<p>Nessuna versione su Internet Archive.</p>";
  }
  let html = '<ul class="risultati">';
  d.versioni.slice(0, 5).forEach((v, i) => {
    const migliore = i === 0;
    const dettagli = [v.altezza ? `${v.altezza}p` : "risoluzione sconosciuta", dimensione(v.dimensione)];
    if (v.originale) dettagli.push("originale");
    const dati = `data-identificativo="${esc(v.identificativo)}" data-nome="${esc(v.nome)}"`;
    html += `<li class="${migliore ? "migliore" : ""}">` +
      `<div class="descrizione">${migliore ? '<span class="badge">La migliore</span>' : ""}` +
      `<strong>${esc(v.titolo_item || v.identificativo)}</strong>` +
      `<span class="sottotitolo">${dettagli.map(esc).join(" · ")}` +
      ` · <a href="${esc(v.pagina)}" target="_blank" rel="noopener">pagina e licenza</a>` +
      `<span class="file"> · ${esc(v.nome)}</span></span></div>` +
      `<div class="azioni">` +
      `<button ${migliore ? "" : 'class="secondario" '}${dati} data-modo="http">Scarica</button>` +
      `<button class="${migliore ? "contorno" : "testo"}" ${dati} data-modo="torrent">Via torrent</button>` +
      "</div></li>";
  });
  return html + "</ul>";
}

function htmlProwlarr(d) {
  if (d.avviso) {
    return `<div class="nota"><strong>${esc(d.avviso)}</strong><p>${esc(d.cosa_fare || "")}</p></div>`;
  }
  if (!d.risultati.length) return `<p>Nessun risultato negli indexer (${d.indexer} attivi).</p>`;
  let html = '<ul class="risultati">';
  for (const r of d.risultati.slice(0, 25)) {
    html += "<li>" +
      `<div class="descrizione"><strong>${esc(r.titolo)}</strong>` +
      `<span class="sottotitolo">${dimensione(r.dimensione)} · seed ${r.seed ?? "?"} · peer ${r.peer ?? "?"} · ${esc(r.indexer)}` +
      `${r.data ? " · " + esc(r.data) : ""}</span></div>` +
      `<div class="azioni"><button class="secondario" data-risultato="${esc(r.id)}">Scarica</button></div></li>`;
  }
  return html + "</ul>";
}

// --- ricerca di un film da associare (torrent manuali) ------------------------
function cercatoreFilm(campo, bottone, contenitore, quandoScelto) {
  async function cerca() {
    const q = campo.value.trim();
    if (!q) return;
    contenitore.innerHTML = '<p class="caricamento">Cerco…</p>';
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
      setTimeout(() => { window.location.href = BASE + "/download"; }, 1200);
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
      `<span class="barra"><span style="width:${d.percentuale}%"></span></span><span class="sottotitolo">${riga}</span></span></a>`;
  }
  const barra = d.stato === "in_corso"
    ? `<div class="barra"><span style="width:${d.percentuale}%"></span></div>` +
      `<span class="sottotitolo">${Math.round(d.percentuale)}% · ${esc(d.scaricati_testo)} di ${esc(d.totale_testo)}` +
      `${d.velocita_testo ? " · " + esc(d.velocita_testo) : ""}</span>`
    : "";
  let azioni = "";
  if (d.stato === "da_associare") azioni += `<button data-associa="${d.id}" data-nome="${esc(d.titolo)}">Scegli il film</button>`;
  if (d.stato === "errore") azioni += `<button data-riprova="${d.id}">Riprova</button>`;
  if (d.stato === "errore") azioni += `<button class="secondario" data-associa="${d.id}" data-nome="${esc(d.titolo)}">Cambia film</button>`;
  if (d.stato !== "importazione") {
    const etichetta = d.stato === "importato" ? "Togli dall'elenco" : "Annulla";
    azioni += `<button class="secondario" data-annulla="${d.id}" data-stato="${d.stato}">${etichetta}</button>`;
  }
  return `<div class="download stato-${d.stato}">` +
    (d.poster ? `<img src="${esc(d.poster)}" alt="">` : htmlSenzaPoster(d.titolo, d.anno)) +
    `<div class="descrizione"><strong>${esc(d.titolo)}${d.anno ? " (" + esc(d.anno) + ")" : ""}</strong>` +
    `<span class="riga-stato"><span class="stato">${esc(d.stato_testo)}</span> <span class="sottotitolo">· ${esc(fonte)}</span></span>` +
    barra + (d.messaggio ? `<p class="messaggio">${esc(d.messaggio)}</p>` : "") +
    `</div><div class="azioni">${azioni}</div></div>`;
}

function avviaDownload({ soloAttivi }) {
  const contenitore = document.getElementById(soloAttivi ? "elenco-attivi" : "elenco-download");
  const sezione = document.getElementById("attivi");

  async function aggiorna() {
    try {
      const d = await chiama("/api/download");
      let elenco = d.download;
      if (soloAttivi) elenco = elenco.filter((x) => ["in_corso", "completato", "importazione", "da_associare", "errore"].includes(x.stato));
      if (soloAttivi) sezione.hidden = !elenco.length;
      contenitore.innerHTML = elenco.length ? elenco.slice(0, soloAttivi ? 3 : 60).map((x) => htmlDownload(x, soloAttivi)).join("")
        : '<p class="vuoto">Nessun download. Cerca un film o aggiungi un torrent.</p>';
    } catch (e) {
      if (!soloAttivi) contenitore.innerHTML = htmlErrore(e);
    }
  }
  aggiorna();
  setInterval(aggiorna, 4000);

  contenitore.addEventListener("click", async (ev) => {
    const b = ev.target.closest("button");
    if (!b) return;
    try {
      if (b.dataset.riprova) {
        avviso((await chiama(`/api/download/${b.dataset.riprova}/riprova`, { method: "POST" })).messaggio);
      } else if (b.dataset.annulla) {
        const domanda = b.dataset.stato === "importato"
          ? "Tolgo il download dall'elenco? Il film resta in Jellyfin."
          : "Annullo il download e cancello i file scaricati finora?";
        if (!confirm(domanda)) return;
        avviso((await chiama(`/api/download/${b.dataset.annulla}/annulla`, { method: "POST" })).messaggio);
      } else if (b.dataset.associa) {
        apriAssocia(Number(b.dataset.associa), b.dataset.nome);
      }
      aggiorna();
    } catch (e) { avviso(e.errore + " " + (e.cosa_fare || ""), true); }
  });

  function apriAssocia(idDownload, nome) {
    const dialogo = document.getElementById("dialogo-associa");
    if (!dialogo) { window.location.href = BASE + "/download"; return; }
    document.getElementById("associa-nome").textContent = nome;
    const campo = document.getElementById("associa-cerca");
    campo.value = nome.replace(/[._]/g, " ").replace(/\b(1080p|720p|2160p|x264|x265|h264|bluray|web-?dl|ita|eng)\b.*$/i, "").trim();
    dialogo.dataset.download = idDownload;
    document.getElementById("associa-scelte").innerHTML = "";
    dialogo.showModal();
  }

  if (!soloAttivi && !avviaDownload._cercatore) {
    avviaDownload._cercatore = true;
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
  }
}

// --- novità nelle pagine iniziali dei mondi -------------------------------------------
function htmlScheda(f) {
  let link = `${BASE}/film/${f.id}`;
  let sotto = esc(f.anno || "");
  if (f.serie) link = `/sonarr/add/new?term=${encodeURIComponent(f.titolo)}`;
  if (f.sottotitolo) {  // episodio da Sonarr
    link = "/jellyfin/";
    sotto = `${esc(f.sottotitolo)}${f.pronto ? ' · <span class="pronto">pronto</span>' : ""}`;
  }
  return `<a class="scheda" href="${link}">` +
    (f.poster ? `<img src="${esc(f.poster)}" alt="" loading="lazy">` : htmlSenzaPoster(f.titolo, f.anno)) +
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

function htmlTop(f, i) {
  const link = f.serie ? `/sonarr/add/new?term=${encodeURIComponent(f.titolo)}` : `${BASE}/film/${f.id}`;
  return `<a class="scheda-top" href="${link}" aria-label="${i + 1}. ${esc(f.titolo)}">` +
    `<span class="numero" aria-hidden="true">${i + 1}</span>` +
    (f.poster ? `<img src="${esc(f.poster)}" alt="" loading="lazy">` : htmlSenzaPoster(f.titolo, f.anno)) + "</a>";
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
  const link = tutti ? `<a class="link-accento" href="${tutti}">Vedi tutti →</a>` : "";
  const id = `riga-${r.tipo}-${r.genere || r.piattaforma || ""}`;
  const logo = r.logo ? `<img class="logo-piattaforma" src="${esc(r.logo)}" alt="" width="36" height="36" onerror="this.remove()">` : "";
  return `<section class="riga riga-${esc(r.tipo)}" aria-labelledby="${id}">` +
    `<div class="riga-testa"><h2 id="${id}">${logo}${esc(r.titolo)}</h2>${link}</div>` +
    `<div class="striscia${r.tipo === "top10" ? " striscia-top" : ""}">${schede}</div></section>`;
}

async function avviaHome(mondo = "film") {
  const contenitore = document.getElementById("righe");
  if (!contenitore) return;
  contenitore.innerHTML = '<p class="caricamento">Carico i consigli…</p>';
  try {
    const d = await chiama(`/api/home?mondo=${encodeURIComponent(mondo)}`);
    contenitore.innerHTML = d.righe.map((r) => htmlRiga(r, mondo)).join("") +
      (d.errore && !d.righe.length ? htmlErrore(d.errore) : "") +
      `<div class="centrato"><button type="button" class="secondario" id="scegli-piattaforme">Scegli le tue piattaforme</button></div>`;
    document.getElementById("scegli-piattaforme").addEventListener("click", () => apriScegliPiattaforme(mondo));
  } catch (e) {
    contenitore.innerHTML = htmlErrore(e);
  }
  contenitore.removeAttribute("aria-busy");
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
  elenco.innerHTML = '<p class="caricamento">Carico…</p>';
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
    elenco.innerHTML = '<li class="caricamento">Controllo i servizi… (al massimo 10 secondi)</li>';
    riassunto.textContent = "";
    try {
      const d = await chiama("/api/diagnosi", { attesa: 30000 });
      const icona = { ok: "✓", lento: "!", errore: "✗" };
      elenco.innerHTML = d.risultati.map((r) =>
        `<li class="servizio-stato ${esc(r.stato)}"><span class="icona-stato" aria-hidden="true">${icona[r.stato] || "?"}</span>` +
        `<div><strong>${esc(r.nome)}</strong> <span class="sottotitolo">serve per ${esc(r.a_cosa_serve)}</span>` +
        `<p>${esc(r.messaggio)}${r.millisecondi ? ` <span class="sottotitolo">(${(r.millisecondi / 1000).toFixed(1).replace(".", ",")} s)</span>` : ""}</p>` +
        (r.cosa_fare ? `<p class="cosa-fare">${esc(r.cosa_fare)}</p>` : "") + "</div></li>").join("");
      const male = d.risultati.filter((r) => r.stato === "errore").map((r) => r.nome);
      riassunto.textContent = d.tutto_ok ? "Tutto a posto: tutti i servizi rispondono."
        : male.length ? `Non rispondono: ${male.join(", ")}.` : "Rispondono tutti, ma qualcuno è lento.";
    } catch (e) {
      elenco.innerHTML = `<li>${htmlErrore(e)}</li>`;
    }
    bottone.disabled = false;
  }
  bottone.addEventListener("click", controlla);
  controlla();
}

// --- pagina Musica --------------------------------------------------------------------
function tintaDalNome(nome) {
  let h = 0;
  for (const c of nome) h = (h * 31 + c.codePointAt(0)) % 360;
  return h;
}

function htmlArtista(a, dettaglio) {
  const nome = (a.nome || "?").trim();
  const iniziale = esc(nome.replace(/^the\s+/i, "").charAt(0).toUpperCase());
  const azione = a.in_libreria
    ? '<span class="sottotitolo">già in libreria</span>'
    : (window.LIDARR ? `<button class="secondario" data-mbid="${esc(a.mbid)}">Aggiungi</button>` : "");
  return `<div class="artista"><div class="iniziale" style="--h:${tintaDalNome(nome)}">${iniziale}</div>` +
    `<div class="descrizione"><strong>${esc(a.nome)}</strong>` +
    `<span class="sottotitolo">${esc(dettaglio)}</span>` +
    `<a class="sottotitolo" href="https://listenbrainz.org/artist/${esc(a.mbid)}" target="_blank" rel="noopener">anteprima ↗</a>` +
    `</div>${azione}</div>`;
}

function avviaMusica() {
  const perTe = document.getElementById("per-te");
  const tendenze = document.getElementById("tendenze-musica");
  chiama("/api/musica/per-te").then((d) => {
    if (d.avviso) { perTe.innerHTML = `<div class="nota"><p>${esc(d.avviso)}</p></div>`; return; }
    document.getElementById("partenza").textContent = d.partenza.length
      ? "Partendo da: " + d.partenza.join(", ") : "";
    perTe.innerHTML = d.suggerimenti.length
      ? d.suggerimenti.map((a) => htmlArtista(a, "simile a " + a.simile_a.join(", "))).join("")
      : "<p>Nessun suggerimento per ora: ascolta qualche brano in Jellyfin e riprova.</p>";
  }).catch((e) => { perTe.innerHTML = htmlErrore(e); });

  chiama("/api/musica/tendenze").then((d) => {
    tendenze.innerHTML = d.artisti.map((a) =>
      htmlArtista(a, Number(a.ascolti).toLocaleString("it-IT") + " ascolti")).join("");
  }).catch((e) => { tendenze.innerHTML = htmlErrore(e); });

  document.querySelector("main").addEventListener("click", async (ev) => {
    const b = ev.target.closest("button[data-mbid]");
    if (!b) return;
    b.disabled = true;
    try {
      const r = await inviaJSON("/api/musica/lidarr", { mbid: b.dataset.mbid });
      avviso(r.messaggio);
      b.textContent = "✓ Aggiunto";
    } catch (e) { avviso(e.errore + " " + (e.cosa_fare || ""), true); b.disabled = false; }
  });
}

// --- pagina Spazio ----------------------------------------------------------------
function avviaSpazio() {
  const anteprima = document.getElementById("anteprima");
  const registro = document.getElementById("registro");
  const bottone = document.getElementById("libera");

  function voce(testo, byte, dettaglio) {
    return `<li><div class="descrizione"><strong>${esc(testo)}</strong>` +
      `<span class="sottotitolo">${esc(dettaglio)}</span></div><div class="azioni">${dimensione(byte)}</div></li>`;
  }

  async function carica() {
    try {
      const d = await chiama("/api/pulizia");
      let html = "";
      if (d.abbandonati.length) {
        html += "<h3>Download abbandonati (tolti sempre)</h3><ul class=\"risultati\">" +
          d.abbandonati.map((a) => voce(a.nome, a.dimensione, `fermo da ${a.giorni} giorni, non lo usa nessun download`)).join("") + "</ul>";
      }
      if (d.pulizia_visti) {
        html += `<h3>Già visti da più di ${d.giorni} giorni (tolti solo se serve spazio, dal più vecchio)</h3>`;
        html += d.visti.length
          ? "<ul class=\"risultati\">" + d.visti.map((v) => voce(v.serie ? `${v.serie} – ${v.nome}` : v.nome, v.dimensione,
              `visto ${v.giorni} giorni fa`)).join("") + "</ul>"
          : "<p>Nessun film o episodio visto da abbastanza tempo.</p>";
      } else {
        html += '<div class="nota"><p>La pulizia dei film e degli episodi già visti è spenta. Puoi attivarla con ' +
          '<code>mediabox impostazioni</code>: tocca solo quello che è già stato visto da tempo, mai i preferiti.</p></div>';
      }
      if (!d.abbandonati.length && !(d.pulizia_visti && d.visti.length)) html = "<p>Niente da togliere: tutto in ordine. 👍</p>" + html;
      html += `<p class="sottotitolo">Totale che si potrebbe liberare: <strong>${dimensione(d.totale)}</strong></p>`;
      anteprima.innerHTML = html;
      bottone.disabled = !d.totale;
      registro.innerHTML = d.registro.length
        ? "<ul class=\"risultati\">" + d.registro.map((r) => voce(r.cosa, r.dimensione,
            new Date(r.quando * 1000).toLocaleString("it-IT") + " · " + (r.motivo || ""))).join("") + "</ul>"
        : "<p>Nessuna pulizia finora.</p>";
    } catch (e) { anteprima.innerHTML = htmlErrore(e); registro.innerHTML = ""; }
  }

  bottone.addEventListener("click", async () => {
    if (!confirm("Tolgo i download abbandonati" + " e, se serve spazio e la pulizia dei visti è attiva, i film ed episodi visti da tempo. Continuo?")) return;
    bottone.disabled = true;
    try { avviso((await chiama("/api/pulizia", { method: "POST" })).messaggio); }
    catch (e) { avviso(e.errore + " " + (e.cosa_fare || ""), true); }
    carica();
  });
  carica();
}
