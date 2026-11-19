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

async function chiama(percorso, opzioni = {}) {
  let risposta;
  try {
    risposta = await fetch(BASE + percorso, opzioni);
  } catch (e) {
    throw { errore: "Cerca non risponde.", cosa_fare: "Controlla la connessione e ricarica la pagina." };
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
    (e.cosa_fare ? `<p>${esc(e.cosa_fare)}</p>` : "") + "</div>";
}

// --- pagina del film ------------------------------------------------------
function avviaFilm(tmdbId) {
  const sezione = (id) => document.querySelector(`#${id} .contenuto`);

  // Le tre ricerche partono insieme, ognuna riempie la sua sezione.
  chiama(`/api/film/${tmdbId}/provider`)
    .then((d) => { sezione("provider").innerHTML = htmlProvider(d); })
    .catch((e) => { sezione("provider").innerHTML = htmlErrore(e); });

  chiama(`/api/film/${tmdbId}/archive`)
    .then((d) => { sezione("archive").innerHTML = htmlArchive(d); })
    .catch((e) => { sezione("archive").innerHTML = htmlErrore(e); });

  chiama(`/api/film/${tmdbId}/prowlarr`)
    .then((d) => { sezione("prowlarr").innerHTML = htmlProwlarr(d); })
    .catch((e) => { sezione("prowlarr").innerHTML = htmlErrore(e); });

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

  const avvisami = document.getElementById("avvisami");
  avvisami.addEventListener("click", async () => {
    avvisami.disabled = true;
    try {
      const r = await chiama(`/api/avvisami/${tmdbId}`, { method: "POST" });
      avvisami.textContent = r.attivo ? "✓ Ti avviserò se diventa gratis" : "🔔 Avvisami se diventa disponibile gratis";
      avviso(r.messaggio);
    } catch (e) { avviso(e.errore, true); }
    avvisami.disabled = false;
  });
}

function htmlProvider(d) {
  if (!d.gruppi.length) {
    return "<p>Al momento non risulta disponibile su nessun servizio in Italia.</p>" +
      "<p class=\"sottotitolo\">Puoi usare il pulsante \"Avvisami\" qui sopra.</p>";
  }
  let html = "";
  for (const g of d.gruppi) {
    html += `<div class="gruppo-provider ${g.tipo === "ads" || g.tipo === "free" ? "gratis" : ""}">` +
      `<h3>${esc(g.etichetta)}</h3><div class="loghi">`;
    for (const p of g.provider) {
      html += `<a href="${esc(d.link)}" target="_blank" rel="noopener" title="${esc(p.nome)}">` +
        (p.logo ? `<img src="${esc(p.logo)}" alt="${esc(p.nome)}" loading="lazy">` : esc(p.nome)) + "</a>";
    }
    html += "</div></div>";
  }
  if (d.link) html += `<p><a href="${esc(d.link)}" target="_blank" rel="noopener">Apri la pagina con tutti i link →</a></p>`;
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
    const risoluzione = v.altezza ? `${v.altezza}p` : "risoluzione sconosciuta";
    html += `<li class="${i === 0 ? "migliore" : ""}">` +
      `<div class="descrizione">${i === 0 ? '<span class="badge">Migliore</span> ' : ""}` +
      `<strong>${esc(v.titolo_item || v.identificativo)}</strong><br>` +
      `<span class="sottotitolo">${esc(v.nome)} · ${risoluzione} · ${esc(v.formato)} · ${dimensione(v.dimensione)}` +
      `${v.originale ? " · originale" : ""} · <a href="${esc(v.pagina)}" target="_blank" rel="noopener">pagina e licenza</a></span></div>` +
      `<div class="azioni">` +
      `<button data-identificativo="${esc(v.identificativo)}" data-nome="${esc(v.nome)}" data-modo="http">Scarica</button>` +
      `<button class="secondario" data-identificativo="${esc(v.identificativo)}" data-nome="${esc(v.nome)}" data-modo="torrent">Via torrent</button>` +
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
      `<div class="descrizione"><strong>${esc(r.titolo)}</strong><br>` +
      `<span class="sottotitolo">${dimensione(r.dimensione)} · seed ${r.seed ?? "?"} · peer ${r.peer ?? "?"} · ${esc(r.indexer)}` +
      `${r.data ? " · " + esc(r.data) : ""}</span></div>` +
      `<div class="azioni"><button data-risultato="${esc(r.id)}">Scarica</button></div></li>`;
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
function htmlDownload(d) {
  const fonte = { archive: "Internet Archive", prowlarr: "Indexer", manuale: "Torrent manuale" }[d.fonte] || d.fonte;
  const barra = d.stato === "in_corso"
    ? `<div class="barra"><div style="width:${d.percentuale}%"></div></div>` +
      `<span class="sottotitolo">${d.percentuale}% · ${esc(d.scaricati_testo)} di ${esc(d.totale_testo)}` +
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
    (d.poster ? `<img src="${esc(d.poster)}" alt="">` : '<div class="senza-poster">🎬</div>') +
    `<div class="descrizione"><strong>${esc(d.titolo)}${d.anno ? " (" + esc(d.anno) + ")" : ""}</strong>` +
    `<span class="stato">${esc(d.stato_testo)}</span> <span class="sottotitolo">· ${esc(fonte)}</span>` +
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
      contenitore.innerHTML = elenco.length ? elenco.map(htmlDownload).join("")
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

// --- novità nella pagina iniziale -------------------------------------------------
function htmlScheda(f) {
  return `<a class="scheda" href="${BASE}/film/${f.id}">` +
    (f.poster ? `<img src="${esc(f.poster)}" alt="" loading="lazy">` : '<div class="senza-poster">🎬</div>') +
    `<span class="titolo">${esc(f.titolo)}</span><span class="anno">${esc(f.anno)}</span></a>`;
}

async function avviaNovita() {
  const sezione = document.getElementById("novita");
  try {
    const d = await chiama("/api/novita");
    document.getElementById("tendenze").innerHTML = d.tendenze.map(htmlScheda).join("");
    document.getElementById("cinema").innerHTML = d.cinema.map(htmlScheda).join("");
    sezione.hidden = !(d.tendenze.length || d.cinema.length);
  } catch (e) {
    sezione.hidden = true;  // senza TMDB la pagina resta comunque usabile
  }
}
