// Cerca — movimento e componenti animati. Niente build: usa GSAP (vendor/gsap.min.js) se c'è,
// altrimenti mostra tutto fermo. Con "riduci movimento" attivo si vede subito lo stato finale.
//
// Da dove vengono le idee (vedi vendor/CREDITI.md):
//  - OpenMotion (Apache-2.0): timeline GSAP, linee che si disegnano, rivelazione a maschera dei titoli
//  - kokonutui (MIT): tieni premuto per confermare, campanella, schede con l'indicatore che scorre,
//    avvisi che passano da "in corso" a "fatto", stato vuoto, barre di disponibilità
//  - bklit-ui (MIT): lancetta con righello e zona rossa, linea dal vivo che scorre
"use strict";

const G = window.gsap || null;
const FERMO = !G || window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const CURVA = "power3.out";

// --- icone animate degli stati: al posto delle emoji -------------------------------------
// Ogni stato ha un piccolo disegno che spiega cosa succede. Si ripetono solo se la cosa è viva.
const ICONE_ANIMATE = {
  download: '<g class="ia-freccia"><path d="M12 4v10"/><path d="m8 10 4 4 4-4"/></g><path d="M5 19.5h14"/>',
  coda: '<g class="ia-clessidra"><path d="M7 4h10M7 20h10"/><path d="M8 4c0 4.5 8 5.5 8 8s-8 3.5-8 8M16 4c0 4.5-8 5.5-8 8s8 3.5 8 8"/></g>',
  cerco: '<g class="ia-lente"><circle cx="10.5" cy="10.5" r="5.5"/><path d="m14.5 14.5 4.5 4.5"/></g>',
  importazione: '<path d="M4 20h16"/><g class="ia-quadro"><rect x="7" y="9" width="10" height="7" rx="1.5"/><path d="M10.5 11v3l2.5-1.5z"/></g>',
  pronto: '<circle class="ia-traccia" cx="12" cy="12" r="8.5"/><path class="ia-traccia" d="m8.5 12.2 2.4 2.4 4.8-5"/>',
  problema: '<path class="ia-traccia" d="M12 4.5 20.5 19h-17z"/><path class="ia-punto" d="M12 10v4M12 16.6h.01"/>',
  non_uscito: '<rect x="4.5" y="5.5" width="15" height="14" rx="2"/><path d="M4.5 10h15M8.5 3.5v4M15.5 3.5v4"/><path class="ia-foglio" d="M8 13.5h3M8 16.5h6"/>',
  campanella: '<g class="ia-camp"><path d="M6.5 16V11a5.5 5.5 0 0 1 11 0v5l1.5 2h-14z"/><path d="M10 20.5a2 2 0 0 0 4 0"/></g>',
  attesa: '<circle cx="12" cy="12" r="8.5" opacity=".25"/><path class="ia-arco" d="M12 3.5a8.5 8.5 0 0 1 8.5 8.5"/>',
  errore: '<circle class="ia-traccia" cx="12" cy="12" r="8.5"/><path class="ia-traccia" d="m9 9 6 6m0-6-6 6"/>',
  lento: '<circle class="ia-traccia" cx="12" cy="12" r="8.5"/><path class="ia-traccia" d="M12 7.5V12l3 2"/>',
};
ICONE_ANIMATE.attivo = ICONE_ANIMATE.download;
ICONE_ANIMATE.ok = ICONE_ANIMATE.pronto;
ICONE_ANIMATE.da_associare = ICONE_ANIMATE.problema;
ICONE_ANIMATE.in_arrivo = ICONE_ANIMATE.non_uscito;

function iconaAnimata(stato) {
  const disegno = ICONE_ANIMATE[stato];
  return disegno ? `<svg class="ia" viewBox="0 0 24 24" data-ia="${stato}" aria-hidden="true">${disegno}</svg>` : "";
}

function disegnaLinee(el, durata = 0.55, ritardo = 0) {
  const tl = G ? G.timeline({ delay: ritardo }) : null;
  el.querySelectorAll(".ia-traccia, .disegna").forEach((p, i) => {
    if (!p.getTotalLength || !tl) return;
    const L = p.getTotalLength();
    G.set(p, { strokeDasharray: L, strokeDashoffset: L });
    tl.to(p, { strokeDashoffset: 0, duration: durata, ease: "power2.inOut" }, i * 0.16);
  });
  return tl;
}

const avviate = new WeakSet();
function animaIcona(el, ritardo = 0) {
  if (FERMO || avviate.has(el) || !el.isConnected) return;
  avviate.add(el);
  const q = (s) => el.querySelector(s);
  switch (el.dataset.ia) {
    case "download": case "attivo":
      G.fromTo(q(".ia-freccia"), { y: -7, opacity: 0 }, { keyframes: [{ y: 0, opacity: 1, duration: 0.5, ease: "power2.out" },
        { y: 3, opacity: 0, duration: 0.45, ease: "power1.in", delay: 0.25 }], repeat: -1, repeatDelay: 0.15, delay: ritardo });
      break;
    case "coda":
      G.to(q(".ia-clessidra"), { rotation: 180, transformOrigin: "50% 50%", duration: 0.7, ease: "power3.inOut", repeat: -1, repeatDelay: 1.6, delay: ritardo });
      break;
    case "cerco":
      G.fromTo(q(".ia-lente"), { x: -2.5 }, { x: 2.5, y: -1.5, duration: 0.9, ease: "sine.inOut", yoyo: true, repeat: -1, delay: ritardo });
      break;
    case "importazione":
      G.fromTo(q(".ia-quadro"), { y: -9, opacity: 0 }, { keyframes: [{ y: 0, opacity: 1, duration: 0.55, ease: CURVA },
        { opacity: 0, duration: 0.3, delay: 0.9 }], repeat: -1, delay: ritardo });
      break;
    case "pronto": case "ok": case "errore": case "lento":
      disegnaLinee(el, 0.5, ritardo);
      break;
    case "problema": case "da_associare":
      disegnaLinee(el, 0.6, ritardo);
      G.fromTo(q(".ia-punto"), { opacity: 0 }, { opacity: 1, duration: 0.2, delay: ritardo + 0.55 });
      G.fromTo(el, { x: 0 }, { keyframes: [{ x: -1.5 }, { x: 1.5 }, { x: -1 }, { x: 0 }], duration: 0.3, delay: ritardo + 0.7 });
      break;
    case "non_uscito": case "in_arrivo":
      G.fromTo(q(".ia-foglio"), { scaleY: 0, transformOrigin: "50% 0%" }, { scaleY: 1, duration: 0.5, ease: CURVA, delay: ritardo + 0.2 });
      break;
    case "attesa":
      G.to(q(".ia-arco"), { rotation: 360, transformOrigin: "12px 12px", duration: 0.9, ease: "none", repeat: -1 });
      break;
    default:
  }
}

// La campanella di "Avvisami" oscilla una volta (bell-button di kokonutui)
function suonaCampanella(el) {
  if (FERMO || !el) return;
  G.fromTo(el.querySelector(".ia-camp"), { rotation: 0 }, {
    keyframes: [{ rotation: -16 }, { rotation: 13 }, { rotation: -8 }, { rotation: 4 }, { rotation: 0 }],
    duration: 0.7, ease: "power1.inOut", transformOrigin: "12px 4px",
  });
}

// --- pellicola: la barra di avanzamento dei download (l'unico dettaglio decorativo) ----------
function htmlPellicola(percentuale, { viva = false, grande = false, verde = false } = {}) {
  const p = Math.max(0, Math.min(100, Number(percentuale) || 0));
  return `<span class="pellicola${viva ? " viva" : ""}${grande ? " grande" : ""}${verde ? " verde" : ""}" role="progressbar" ` +
    `aria-valuenow="${Math.round(p)}" aria-valuemin="0" aria-valuemax="100" style="--p:${p}%">` +
    '<span class="riempi"></span><span class="fori"></span></span>';
}

const pellicoleViste = new WeakSet();
function scorriPellicole(radice = document) {
  if (FERMO) return;
  radice.querySelectorAll(".pellicola.viva .fori").forEach((f) => {
    if (pellicoleViste.has(f)) return;
    pellicoleViste.add(f);
    G.fromTo(f, { x: 0 }, { x: -10, duration: 0.5, ease: "none", repeat: -1 });
  });
}

// --- tutto quello che compare nella pagina riceve il suo movimento ------------------------
function animaTutto(radice = document) {
  radice.querySelectorAll("svg.ia[data-ia]").forEach((el, i) => animaIcona(el, Math.min(i, 8) * 0.06));
  scorriPellicole(radice);
}

if ("MutationObserver" in window) {
  let inAttesa = false;
  new MutationObserver(() => {
    if (inAttesa) return;
    inAttesa = true;
    requestAnimationFrame(() => { inAttesa = false; animaTutto(document); });
  }).observe(document.documentElement, { childList: true, subtree: true });
}

// --- una sola entrata per pagina: titolo a maschera e poi il resto (OpenMotion: title reveal) ---
function entrataPagina() {
  if (FERMO) return;
  // il titolo della vetrina ha la sua entrata (entraVetrina): due "from" sullo stesso titolo
  // lo lasciavano fermo a metà, tagliato dalla maschera
  const titolo = document.querySelector("main .rivela:not(.vetrina .rivela) > span");
  if (titolo) G.fromTo(titolo, { yPercent: 105 }, { yPercent: 0, duration: 0.6, ease: CURVA });
  const resto = [...document.querySelectorAll("main > .entra, main > section.entra")].slice(0, 6);
  if (resto.length) G.from(resto, { opacity: 0, y: 10, duration: 0.45, ease: CURVA, stagger: 0.05, clearProps: "transform,opacity" });
}

// --- logo: il divano si disegna una volta all'apertura -----------------------------------
function disegnaLogo() {
  if (FERMO) return;
  const svg = document.querySelector(".logo-segno svg");
  if (!svg || sessionStorage.getItem("cerca:logo")) return;
  try { sessionStorage.setItem("cerca:logo", "1"); } catch (e) { /* niente */ }
  svg.querySelectorAll("path").forEach((p) => p.classList.add("disegna"));
  disegnaLinee(svg, 0.7, 0.1);
}

// --- avvisi in basso (promise-toast di kokonutui): attesa → fatto o errore ----------------
function mostraAvviso(testo, stato = "pronto", durata = 5000) {
  const box = document.getElementById("avviso");
  if (!box) return;
  box.className = `avviso${stato === "errore" ? " errore" : ""}`;
  box.innerHTML = iconaAnimata(stato === "errore" ? "errore" : stato) + `<span>${esc(testo)}</span>`;
  box.hidden = false;
  if (!FERMO) { G.killTweensOf(box); G.fromTo(box, { opacity: 0, y: 8 }, { opacity: 1, y: 0, duration: 0.25, ease: CURVA }); }
  clearTimeout(mostraAvviso._t);
  if (durata) mostraAvviso._t = setTimeout(() => {
    if (FERMO) { box.hidden = true; return; }
    G.to(box, { opacity: 0, y: 8, duration: 0.2, onComplete: () => { box.hidden = true; } });
  }, durata);
}

// --- finestre che entrano (OpenMotion): salgono piano, poi le voci a cascata --------------------
function entraFinestra(d) {
  if (FERMO || !d) return;
  G.fromTo(d, { opacity: 0, y: 18, scale: 0.98 }, { opacity: 1, y: 0, scale: 1, duration: 0.38, ease: CURVA, clearProps: "opacity,transform" });
  const voci = [...d.querySelectorAll(".dettaglio-azioni > *, .dettaglio-voci > *, .serie-puntate > *, .elenco-piattaforme > *, .scelte > *")]
    .slice(0, 12);
  if (voci.length) G.from(voci, { opacity: 0, y: 10, duration: 0.35, ease: CURVA, stagger: 0.04, delay: 0.12, clearProps: "opacity,transform" });
}
if (window.HTMLDialogElement && HTMLDialogElement.prototype.showModal) {
  const apri = HTMLDialogElement.prototype.showModal;
  HTMLDialogElement.prototype.showModal = function showModal() { apri.call(this); entraFinestra(this); };
}

// Mostra "in corso" finché la promessa non finisce, poi il risultato
async function conAvviso(promessa, attesa) {
  mostraAvviso(attesa, "attesa", 0);
  try {
    const r = await promessa;
    mostraAvviso((r && r.messaggio) || "Fatto", "pronto");
    return r;
  } catch (e) {
    mostraAvviso(`${e.errore || "Non ci sono riuscito."} ${e.cosa_fare || ""}`.trim(), "errore", 8000);
    throw e;
  }
}

// --- tieni premuto per confermare (hold-button di kokonutui) -------------------------------
// Al posto delle finestre "Sei sicuro?": il tasto si riempie mentre lo tieni, se lasci prima torna
// indietro. Funziona col dito, col mouse e col telecomando (tieni premuto OK / Invio).
const DURATA_PRESA = 1100;
function tieniPremuto(b, quandoFatto) {
  if (b._presa) return;
  b._presa = true;
  b.classList.add("tieni");
  if (!b.querySelector(".tieni-riempi")) b.insertAdjacentHTML("afterbegin", '<span class="tieni-riempi" aria-hidden="true"></span>');
  const riempi = b.querySelector(".tieni-riempi");
  const etichetta = b.getAttribute("aria-label") || b.textContent.trim();
  b.setAttribute("aria-label", `${etichetta}: tieni premuto per confermare`);
  b.title = "Tieni premuto per confermare";
  let inizio = 0, frame = 0, fatto = false;
  const disegna = (p) => { riempi.style.transform = `scaleX(${p})`; };
  const passo = () => {
    const p = Math.min(1, (performance.now() - inizio) / DURATA_PRESA);
    disegna(p);
    if (p >= 1) { fatto = true; b.classList.add("confermato"); quandoFatto(b); return; }
    frame = requestAnimationFrame(passo);
  };
  const parti = (ev) => {
    if (fatto || b.disabled) return;
    if (ev.type === "keydown") { if (ev.repeat || (ev.key !== "Enter" && ev.key !== " ")) return; ev.preventDefault(); }
    inizio = performance.now();
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(passo);
  };
  const ferma = () => {
    if (fatto) return;
    cancelAnimationFrame(frame);
    if (FERMO) { disegna(0); return; }
    const attuale = Math.min(1, (performance.now() - inizio) / DURATA_PRESA);
    G.fromTo(riempi, { scaleX: attuale }, { scaleX: 0, duration: 0.15 + attuale * 0.35, ease: "power2.out" });
  };
  b.addEventListener("pointerdown", parti);
  b.addEventListener("keydown", parti);
  ["pointerup", "pointerleave", "pointercancel", "keyup", "blur"].forEach((e) => b.addEventListener(e, ferma));
  // un tocco breve non fa niente: lo diciamo, così si capisce che va tenuto premuto
  b.addEventListener("click", (ev) => {
    ev.preventDefault();
    if (!fatto && performance.now() - inizio < DURATA_PRESA) mostraAvviso("Tieni premuto il tasto per confermare", "info", 2500);
  });
  b.addEventListener("contextmenu", (ev) => ev.preventDefault());
}

// I tasti con data-tieni si preparano da soli appena compaiono
function preparaTieni(radice = document, azione) {
  radice.querySelectorAll("button[data-tieni]:not(.tieni)").forEach((b) => tieniPremuto(b, azione));
}

// --- schede con l'indicatore che scorre (smooth-tab di kokonutui) ---------------------------
function indicatoreScorrevole(nav) {
  if (!nav || nav.querySelector(".indicatore")) return;
  const attiva = nav.querySelector('[aria-current="page"]');
  if (!attiva) return;
  nav.insertAdjacentHTML("afterbegin", '<span class="indicatore" aria-hidden="true"></span>');
  const ind = nav.querySelector(".indicatore");
  const metti = (el) => {
    ind.style.width = `${el.offsetWidth}px`;
    ind.style.transform = `translateX(${el.offsetLeft}px)`;
    ind.style.setProperty("--c", getComputedStyle(el).getPropertyValue("--c"));
  };
  metti(attiva);
  nav.classList.add("con-indicatore");
  if ("ResizeObserver" in window) new ResizeObserver(() => metti(nav.querySelector('[aria-current="page"]') || attiva)).observe(nav);
  nav.addEventListener("click", (ev) => {
    const a = ev.target.closest("a");
    if (!a || a === attiva || ev.ctrlKey || ev.metaKey) return;
    if (FERMO) return;
    ev.preventDefault();
    attiva.removeAttribute("aria-current");
    a.setAttribute("aria-current", "page");
    G.to(ind, { x: a.offsetLeft, width: a.offsetWidth, duration: 0.28, ease: "power3.inOut",
      onStart: () => { ind.style.transform = ""; }, onComplete: () => { window.location.href = a.href; } });
  });
}

// --- passaggio dentro una piattaforma: un cerchio del suo colore si apre dal tasto premuto ----
const FONDI_PIATTAFORMA = {
  nfx: "#141414", prv: "#0f171e", dnp: "#0e1121", mxx: "#050816", ntv: "#050a0d", skg: "#07070f", atp: "#000000",
  pmp: "#0a1430", cru: "#0b0b0b", rai: "#071a33", msp: "#0c0816", tvi: "#06122b", dpe: "#00051a",
};
function velo(colore, logo) {
  const v = document.createElement("div");
  v.className = "velo";
  v.style.background = colore;
  v.innerHTML = logo ? `<img src="${esc(logo)}" alt="">` : "";
  document.body.append(v);
  return v;
}
function apriPiattaforma(a, ev) {
  if (FERMO || ev.ctrlKey || ev.metaKey || ev.shiftKey) return;
  const colore = FONDI_PIATTAFORMA[a.dataset.tema] || "#141414";
  ev.preventDefault();
  const logo = a.querySelector("img") ? a.querySelector("img").src : "";
  const r = a.getBoundingClientRect();
  const x = ev.clientX || r.left + r.width / 2, y = ev.clientY || r.top + r.height / 2;
  const v = velo(colore, logo);
  try { sessionStorage.setItem("cerca:velo", JSON.stringify({ colore, logo })); } catch (e) { /* niente */ }
  G.fromTo(v, { clipPath: `circle(0px at ${x}px ${y}px)` },
    { clipPath: `circle(${Math.hypot(innerWidth, innerHeight)}px at ${x}px ${y}px)`, duration: 0.5, ease: "power3.inOut",
      onComplete: () => { window.location.href = a.href; } });
  if (logo) G.fromTo(v.querySelector("img"), { scale: 0.7, opacity: 0 }, { scale: 1, opacity: 1, duration: 0.35, delay: 0.2, ease: CURVA });
}
function chiudiVelo() {
  let dati = null;
  try { dati = JSON.parse(sessionStorage.getItem("cerca:velo") || "null"); sessionStorage.removeItem("cerca:velo"); } catch (e) { /* niente */ }
  if (!dati || FERMO) return;
  const v = velo(dati.colore, dati.logo);
  G.to(v, { opacity: 0, duration: 0.4, delay: 0.1, ease: "power2.out", onComplete: () => v.remove() });
}
document.addEventListener("click", (ev) => {
  const a = ev.target.closest("a[data-tema]");
  if (a) apriPiattaforma(a, ev);
});
window.addEventListener("pageshow", (ev) => { if (ev.persisted) document.querySelectorAll(".velo").forEach((v) => v.remove()); });

// --- salotto: lo stato vuoto dei download (empty-state di kokonutui, disegnato a linea) ------
const SALOTTO = `<svg class="salotto" viewBox="0 0 360 200" aria-hidden="true" fill="none" stroke-linecap="round" stroke-linejoin="round">
  <rect class="schermo-luce" x="122" y="28" width="116" height="70" rx="4" fill="var(--lampada)" opacity="0"/>
  <g stroke="var(--testo-2)" stroke-width="1.6">
    <path class="disegna" d="M118 24h124a4 4 0 0 1 4 4v74a4 4 0 0 1-4 4H118a4 4 0 0 1-4-4V28a4 4 0 0 1 4-4z"/>
    <path class="disegna" d="M164 106v10M196 106v10M146 116h68"/>
    <path class="disegna" d="M30 186h300"/>
    <path class="disegna" d="M98 178v-30a10 10 0 0 1 10-10h144a10 10 0 0 1 10 10v30"/>
    <path class="disegna" d="M84 186v-30a8 8 0 0 1 16 0v14h160v-14a8 8 0 0 1 16 0v30"/>
    <path class="disegna" d="M314 186V92M300 92h28l-6-26h-16z"/>
  </g>
  <path class="play" d="M172 52v22l18-11z" fill="var(--su-lampada)" opacity="0"/>
  <path class="raggio" d="M314 92 290 150h48z" fill="var(--lampada)" opacity="0"/>
</svg>`;

function accendiSalotto(svg) {
  if (!svg || svg._acceso) return;
  svg._acceso = true;
  const finale = () => {
    G && G.set(svg.querySelectorAll(".disegna"), { strokeDashoffset: 0 });
    svg.querySelector(".schermo-luce").setAttribute("opacity", ".9");
    svg.querySelector(".play").setAttribute("opacity", "1");
    svg.querySelector(".raggio").setAttribute("opacity", ".12");
  };
  if (FERMO) { finale(); return; }
  const tl = disegnaLinee(svg, 0.55, 0.15);
  tl.to(svg.querySelector(".raggio"), { attr: { opacity: 0.12 }, duration: 0.5 }, "-=.2")
    .to(svg.querySelector(".schermo-luce"), { attr: { opacity: 0.9 }, duration: 0.6, ease: "power2.out" }, "-=.1")
    .fromTo(svg.querySelector(".play"), { attr: { opacity: 0 }, scale: 0.6, transformOrigin: "50% 50%" },
      { attr: { opacity: 1 }, scale: 1, duration: 0.35, ease: CURVA }, "-=.25");
}

// --- cifra che scorre verso il valore nuovo (chart-stat-flow di bklit-ui) ---------------------
function cifraCheScorre(el, valore, formato = (v) => String(Math.round(v))) {
  if (FERMO) { el.textContent = formato(valore); return; }
  const da = Number(el.dataset.valore || 0);
  const o = { v: da };
  el.dataset.valore = valore;
  G.to(o, { v: valore, duration: 1, ease: CURVA, onUpdate: () => { el.textContent = formato(o.v); } });
}

// --- lancetta del disco (gauge di bklit-ui): arco, righello, zona rossa della pulizia --------
function htmlLancetta(usatoPercento, sogliaLibero) {
  const W = 320, H = 190, cx = 160, cy = 168, r = 132;
  const punto = (t, raggio = r) => {
    const a = Math.PI * (1 - t);
    return [cx + raggio * Math.cos(a), cy - raggio * Math.sin(a)];
  };
  const arco = (t0, t1, raggio = r) => {
    const [x0, y0] = punto(t0, raggio), [x1, y1] = punto(t1, raggio);
    return `M${x0.toFixed(1)} ${y0.toFixed(1)}A${raggio} ${raggio} 0 0 1 ${x1.toFixed(1)} ${y1.toFixed(1)}`;
  };
  const soglia = 1 - sogliaLibero / 100;
  let righello = "";
  for (let i = 0; i <= 20; i++) {
    const t = i / 20, lungo = i % 5 === 0;
    const [x0, y0] = punto(t, r + 12), [x1, y1] = punto(t, r + (lungo ? 22 : 17));
    righello += `<path d="M${x0.toFixed(1)} ${y0.toFixed(1)}L${x1.toFixed(1)} ${y1.toFixed(1)}" class="${lungo ? "tacca lunga" : "tacca"}"/>`;
  }
  const u = Math.max(0, Math.min(100, usatoPercento)) / 100;
  return `<svg class="lancetta" viewBox="-12 -16 ${W + 24} ${H + 20}" role="img" aria-label="Disco usato al ${Math.round(usatoPercento)}%">
    ${righello}
    <path d="${arco(0, 1)}" class="binario"/>
    <path d="${arco(soglia, 1)}" class="zona"/>
    <path d="${arco(0, Math.max(u, 0.001))}" class="usato${u >= soglia ? " pieno" : ""}" pathLength="1"/>
    <g class="ago" data-centro="${cx} ${cy}" data-angolo="${(u * 180 - 90).toFixed(1)}">
      <path d="M${cx} ${cy - r + 26}L${cx - 4} ${cy}L${cx + 4} ${cy}z"/><circle cx="${cx}" cy="${cy}" r="7"/>
    </g>
  </svg>`;
}
function muoviLancetta(svg) {
  if (!svg) return;
  const ago = svg.querySelector(".ago"), angolo = Number(ago.dataset.angolo), usato = svg.querySelector(".usato");
  const [cx, cy] = ago.dataset.centro.split(" ");
  if (FERMO) { ago.setAttribute("transform", `rotate(${angolo} ${cx} ${cy})`); return; }
  G.fromTo(ago, { rotation: -90, svgOrigin: `${cx} ${cy}` }, { rotation: angolo, svgOrigin: `${cx} ${cy}`, duration: 1.2, ease: "power3.out", delay: 0.15 });
  G.fromTo(usato, { strokeDasharray: "0 1" }, { strokeDasharray: "1 0", duration: 1.2, ease: "power3.out", delay: 0.15 });
}

// --- linea dal vivo della velocità (live-line-chart di bklit-ui) ----------------------------
// Tiene gli ultimi campioni e scorre a sinistra a ogni nuovo valore.
function lineaDalVivo(contenitore, { campioni = 40, formato = (v) => v } = {}) {
  const valori = [];
  contenitore.innerHTML = '<svg class="linea-viva" preserveAspectRatio="none" aria-hidden="true"></svg><span class="linea-valore num"></span>';
  const svg = contenitore.querySelector("svg"), etichetta = contenitore.querySelector(".linea-valore");
  function disegna() {
    const W = contenitore.clientWidth || 300, H = contenitore.clientHeight || 72;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    if (valori.length < 2) { svg.innerHTML = ""; return; }
    const massimo = Math.max(...valori) * 1.15 || 1;
    const passo = W / (campioni - 1);
    const pt = valori.map((v, i) => [W - (valori.length - 1 - i) * passo, H - 4 - (v / massimo) * (H - 12)]);
    const linea = pt.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join("");
    const [ux, uy] = pt[pt.length - 1];
    svg.innerHTML = `<defs><linearGradient id="sfuma-velocita" x1="0" x2="0" y1="0" y2="1">` +
      `<stop offset="0" stop-color="var(--lampada)" stop-opacity=".28"/><stop offset="1" stop-color="var(--lampada)" stop-opacity="0"/></linearGradient></defs>` +
      `<path d="M0 ${H - 4}H${W}" class="griglia-linea"/><path d="${linea}L${ux.toFixed(1)} ${H}L${pt[0][0].toFixed(1)} ${H}Z" fill="url(#sfuma-velocita)"/>` +
      `<path d="${linea}" class="tratto"/><circle cx="${ux.toFixed(1)}" cy="${uy.toFixed(1)}" r="3.5" class="testa"/>`;
  }
  return {
    aggiungi(v) {
      valori.push(Math.max(0, Number(v) || 0));
      if (valori.length > campioni) valori.shift();
      disegna();
      etichetta.textContent = formato(valori[valori.length - 1]);
      if (!FERMO) G.fromTo(svg, { x: (contenitore.clientWidth || 300) / (campioni - 1) }, { x: 0, duration: 0.6, ease: "power2.out" });
    },
  };
}

// --- barre della disponibilità (uptime-card di kokonutui) per il Controllo servizi -----------
// Ricordiamo nel browser gli ultimi controlli di ogni servizio, così si vede se "va e viene".
function storicoServizio(nome, stato) {
  let tutti = {};
  try { tutti = JSON.parse(localStorage.getItem("cerca:storico-servizi") || "{}"); } catch (e) { tutti = {}; }
  const elenco = (tutti[nome] || []).concat(stato ? [stato] : []).slice(-24);
  if (stato) {
    tutti[nome] = elenco;
    try { localStorage.setItem("cerca:storico-servizi", JSON.stringify(tutti)); } catch (e) { /* niente */ }
  }
  return elenco;
}
function htmlStorico(elenco) {
  const vuoti = Math.max(0, 24 - elenco.length);
  return `<span class="storico" aria-label="Ultimi ${elenco.length} controlli">` +
    "<i></i>".repeat(vuoti) + elenco.map((s) => `<i class="s-${esc(s)}"></i>`).join("") + "</span>";
}

// --- trailer al passaggio, come nelle app di streaming --------------------------------------
// Fermandoti ~0,7 s su una locandina (col mouse o col telecomando) la scheda si allarga e parte
// il trailer muto in loop. Ne carichiamo uno alla volta, e solo quando serve.
const ATTESA_TRAILER = 700;
const trailerNoti = new Map();
let trailerAttivo = null;

function urlTrailer(id, { audio = false } = {}) {
  const p = new URLSearchParams({ autoplay: "1", mute: audio ? "0" : "1", controls: "0", loop: "1", playlist: id,
    playsinline: "1", modestbranding: "1", rel: "0", iv_load_policy: "3", disablekb: "1", enablejsapi: "1" });
  return `https://www.youtube-nocookie.com/embed/${encodeURIComponent(id)}?${p}`;
}

async function trovaTrailer(chiave) {
  if (trailerNoti.has(chiave)) return trailerNoti.get(chiave);
  const [tipo, id] = chiave.split(":");
  const promessa = chiama(`/api/trailer?tipo=${encodeURIComponent(tipo)}&id=${encodeURIComponent(id)}`, { attesa: 8000 })
    .then((d) => d.youtube || "").catch(() => "");
  trailerNoti.set(chiave, promessa);
  return promessa;
}

function fermaTrailer() {
  if (!trailerAttivo) return;
  const { scheda, video } = trailerAttivo;
  trailerAttivo = null;
  scheda.classList.remove("in-anteprima");
  if (FERMO) { video.remove(); return; }
  G.to(video, { opacity: 0, duration: 0.2, onComplete: () => video.remove() });
}

function avviaTrailerSu(scheda) {
  const chiave = scheda.dataset.trailer;
  const contenitore = scheda.querySelector(".poster, .poster-top");
  if (!chiave || !contenitore) return;
  trovaTrailer(chiave).then((id) => {
    if (!id || scheda._sopra !== true) return;
    fermaTrailer();
    const video = document.createElement("span");
    video.className = "trailer";
    video.innerHTML = `<iframe src="${urlTrailer(id)}" title="Trailer" allow="autoplay; encrypted-media" tabindex="-1" loading="eager"></iframe>`;
    contenitore.append(video);
    scheda.classList.add("in-anteprima");
    trailerAttivo = { scheda, video };
    if (!FERMO) G.fromTo(video, { opacity: 0 }, { opacity: 1, duration: 0.4, delay: 0.5 });  // il tempo di partire
  });
}

function sopraScheda(scheda) {
  if (scheda._sopra) return;
  scheda._sopra = true;
  clearTimeout(scheda._timer);
  scheda._timer = setTimeout(() => avviaTrailerSu(scheda), ATTESA_TRAILER);
}
function viaDallaScheda(scheda) {
  scheda._sopra = false;
  clearTimeout(scheda._timer);
  if (trailerAttivo && trailerAttivo.scheda === scheda) fermaTrailer();
}

// Sul telefono niente passaggio: il trailer si apre dalla scheda del film e dalla vetrina.
const CON_PUNTATORE = window.matchMedia("(hover: hover) and (pointer: fine)").matches;
if (CON_PUNTATORE) {
  document.addEventListener("pointerover", (ev) => {
    const s = ev.target.closest("[data-trailer]");
    if (s && !s.contains(ev.relatedTarget)) sopraScheda(s);
  });
  document.addEventListener("pointerout", (ev) => {
    const s = ev.target.closest("[data-trailer]");
    if (s && !s.contains(ev.relatedTarget)) viaDallaScheda(s);
  });
}
// il telecomando (e la tastiera) si "ferma" su una locandina col focus
document.addEventListener("focusin", (ev) => {
  const s = ev.target.closest && ev.target.closest(".striscia [data-trailer]");
  if (s) sopraScheda(s);
});
document.addEventListener("focusout", (ev) => {
  const s = ev.target.closest && ev.target.closest(".striscia [data-trailer]");
  if (s && !s.contains(ev.relatedTarget)) viaDallaScheda(s);
});
document.addEventListener("visibilitychange", () => { if (document.hidden) fermaTrailer(); });

// La vetrina in cima: dopo qualche secondo parte il trailer muto, con il tasto dell'audio
// Sul telefono no: YouTube sopra il video mette i suoi comandi (indietro, pausa, avanti) che coprono
// il titolo, e si risparmiano i dati. Il trailer si apre col tasto "Trailer".
const SOLO_TOCCO = window.matchMedia("(hover: none) and (pointer: coarse)").matches;
function trailerVetrina(vetrina) {
  if (!vetrina || !vetrina.dataset.trailer || document.hidden || SOLO_TOCCO) return;
  setTimeout(async () => {
    const id = await trovaTrailer(vetrina.dataset.trailer);
    if (!id || !vetrina.isConnected) return;
    const fondo = vetrina.querySelector(".vetrina-video");
    if (!fondo) return;
    fondo.innerHTML = `<iframe src="${urlTrailer(id)}" title="Trailer" allow="autoplay; encrypted-media" tabindex="-1"></iframe>`;
    const audio = vetrina.querySelector(".audio-trailer");
    if (audio) {
      audio.hidden = false;
      audio.addEventListener("click", () => {
        const acceso = audio.getAttribute("aria-pressed") !== "true";
        audio.setAttribute("aria-pressed", acceso);
        audio.setAttribute("aria-label", acceso ? "Togli l'audio del trailer" : "Ascolta l'audio del trailer");
        const f = fondo.querySelector("iframe");
        if (f && f.contentWindow) f.contentWindow.postMessage(JSON.stringify({ event: "command", func: acceso ? "unMute" : "mute", args: [] }), "*");
      });
    }
    vetrina.classList.add("con-trailer");
    if (!FERMO) G.fromTo(fondo, { opacity: 0 }, { opacity: 1, duration: 0.8, delay: 0.8 });
  }, 3500);
}

// Trailer a tutto schermo (tasto "Trailer" della scheda film): con audio e comandi
function apriTrailer(chiave) {
  trovaTrailer(chiave).then((id) => {
    if (!id) { mostraAvviso("Per questo titolo non trovo un trailer.", "problema"); return; }
    let d = document.getElementById("dialogo-trailer");
    if (!d) {
      document.body.insertAdjacentHTML("beforeend",
        '<dialog id="dialogo-trailer" class="dialogo-trailer" aria-label="Trailer"><div class="cornice-video"></div>' +
        '<form method="dialog"><button class="secondario">Chiudi</button></form></dialog>');
      d = document.getElementById("dialogo-trailer");
      d.addEventListener("close", () => { d.querySelector(".cornice-video").innerHTML = ""; });
    }
    const p = new URLSearchParams({ autoplay: "1", rel: "0", modestbranding: "1", playsinline: "1" });
    d.querySelector(".cornice-video").innerHTML =
      `<iframe src="https://www.youtube-nocookie.com/embed/${encodeURIComponent(id)}?${p}" title="Trailer" allow="autoplay; encrypted-media; fullscreen" allowfullscreen></iframe>`;
    d.showModal();
  });
}

// --- chi guarda: le facce sotto la lampada --------------------------------------------------
// Entrata a cascata (OpenMotion: timeline), anello che si disegna intorno alla faccia scelta,
// password sbagliata che scuote il campo, e un saluto che accompagna fino in Cerca: la faccia
// vola nell'angolo in alto, dove resta a dire chi sta guardando.
const ANELLO = '<svg class="anello" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">' +
  // un <path> e non un <rect rx>: Safari sbaglia la lunghezza dei rettangoli arrotondati e
  // l'anello restava aperto sugli angoli, come un'animazione mai finita
  '<path d="M16.5 1.5H83.5A15 15 0 0 1 98.5 16.5V83.5A15 15 0 0 1 83.5 98.5H16.5A15 15 0 0 1 1.5 83.5V16.5A15 15 0 0 1 16.5 1.5Z" pathLength="1"/></svg>';
const FONDO_SALOTTO = "rgba(22, 21, 19, 1)";

function avviaChiGuarda() {
  const pagina = document.querySelector(".chi-guarda");
  if (!pagina) return;
  if (!FERMO) {
    const tl = G.timeline({ delay: 0.1 });
    tl.from(".luce-salotto", { opacity: 0, scale: 0.5, duration: 1.2, ease: "power2.out" }, 0)
      .from(".profili > li", { opacity: 0, y: 28, scale: 0.88, duration: 0.65, ease: CURVA, stagger: 0.09,
        clearProps: "transform,opacity" }, 0.25)
      .fromTo(".profili .anello path", { strokeDashoffset: 1 }, { keyframes: [{ strokeDashoffset: 0, duration: 0.45, ease: "power2.inOut" },
        { strokeDashoffset: 1, duration: 0.4, ease: "power2.in", delay: 0.15 }], stagger: 0.09, clearProps: "strokeDashoffset" }, 0.45)
      .from(".accesso-admin, .senza-profili", { opacity: 0, y: 10, duration: 0.4, ease: CURVA, clearProps: "transform,opacity" }, "-=.35");
    // la lampada respira piano, come una luce vera
    G.to(".luce-salotto", { opacity: 0.7, scale: 0.96, duration: 3.4, ease: "sine.inOut", yoyo: true, repeat: -1, delay: 1.4 });
  }
  // col telecomando si parte già dalla prima faccia
  const prima = pagina.querySelector(".profili summary, .profili .tasto-profilo");
  if (prima && !pagina.querySelector("[autofocus]")) setTimeout(() => prima.focus({ preventScroll: true }), FERMO ? 0 : 500);

  pagina.querySelectorAll("details").forEach((d) => d.addEventListener("toggle", () => {
    if (!d.open) return;
    pagina.querySelectorAll("details[open]").forEach((altro) => { if (altro !== d) altro.open = false; });
    const modulo = d.querySelector("form");
    if (modulo && !FERMO) G.from(modulo, { opacity: 0, y: -8, duration: 0.35, ease: CURVA, clearProps: "transform,opacity" });
    const campo = d.querySelector('input[type="password"]');
    if (campo) setTimeout(() => campo.focus(), 60);
  }));
  pagina.querySelectorAll("form").forEach((f) => f.addEventListener("submit", (ev) => entraCome(f, ev)));
  pagina.querySelectorAll('input[type="password"]').forEach((c) => c.addEventListener("input", () => {
    c.classList.remove("sbagliata");
    const msg = c.form && c.form.querySelector(".msg-profilo");
    if (msg) msg.innerHTML = "";
  }));
}

async function entraCome(form, ev) {
  if (!window.fetch || !window.URLSearchParams) return;  // browser vecchio: invio normale
  ev.preventDefault();
  if (form._invio) return;
  form._invio = true;
  let risposta = null, dati = {};
  try {
    risposta = await fetch(form.action, {
      method: "POST", body: new URLSearchParams(new FormData(form)), headers: { "X-Cerca": "accesso" },
    });
    dati = await risposta.json();
  } catch (e) {
    dati = { errore: "Cerca non risponde: controlla la connessione e riprova." };
  }
  form._invio = false;
  if (!risposta || !risposta.ok || !dati.vai) { accessoNegato(form, dati.errore || "Non sono riuscito a entrare: riprova."); return; }
  const sorgente = (form.closest("details") && form.closest("details").querySelector("summary")) ||
    form.querySelector(".tasto-profilo") || form;
  salutaEVai(sorgente.querySelector(".faccia"), sorgente.dataset.nome || form.dataset.nome || "",
    sorgente.dataset.iniziali || form.dataset.iniziali || "", dati.vai, sorgente.dataset.foto || "");
}

function accessoNegato(form, testo) {
  const msg = form.querySelector(".msg-profilo");
  const campo = form.querySelector('input[type="password"]');
  if (msg) msg.innerHTML = iconaAnimata("problema") + `<span>${esc(testo)}</span>`;
  if (!campo) return;
  campo.classList.add("sbagliata");
  campo.value = "";
  campo.focus();
  if (!FERMO) G.fromTo(campo, { x: 0 }, { keyframes: [{ x: -9 }, { x: 8 }, { x: -6 }, { x: 4 }, { x: -2 }, { x: 0 }], duration: 0.42, ease: "power1.out" });
}

function htmlBenvenuto(nome, iniziali, foto = "") {
  const v = document.createElement("div");
  v.className = "benvenuto";
  v.setAttribute("aria-hidden", "true");
  const img = foto ? `<img class="foto" src="${esc(foto)}" alt="" onerror="this.remove()">` : "";
  v.innerHTML = `<div class="luce"></div><span class="faccia">${img}${ANELLO}<span class="lettere">${esc(iniziali)}</span></span>` +
    `<span class="saluto"><span>Ciao, ${esc(nome)}</span></span>`;
  document.body.append(v);
  return v;
}

function centro(r) { return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; }

function salutaEVai(faccia, nome, iniziali, vai, foto = "") {
  try { sessionStorage.setItem("cerca:benvenuto", JSON.stringify({ nome, iniziali, foto })); } catch (e) { /* niente */ }
  if (FERMO) { window.location.href = vai; return; }
  const pagina = document.querySelector(".chi-guarda");
  const altre = [...pagina.querySelectorAll(".profili > li")].filter((li) => !faccia || !li.contains(faccia));
  const v = htmlBenvenuto(nome, iniziali, foto);
  const nuova = v.querySelector(".faccia");
  const arrivo = centro(nuova.getBoundingClientRect());
  const da = faccia ? faccia.getBoundingClientRect() : null;
  const partenza = da ? centro(da) : arrivo;
  const tl = G.timeline({ onComplete: () => { window.location.href = vai; } });
  // le altre facce si fanno da parte, la scelta resta sotto la lampada
  tl.to(altre, { opacity: 0, scale: 0.85, y: 12, duration: 0.35, ease: "power2.in", stagger: 0.04 }, 0)
    .to(pagina.querySelectorAll(".titolo-pagina, .accesso-admin, .senza-profili"), { opacity: 0, y: -10, duration: 0.3, ease: "power2.in" }, 0)
    // il salotto si chiude intorno alla faccia (come il passaggio nelle piattaforme)
    .fromTo(v, { clipPath: `circle(0px at ${partenza.x}px ${partenza.y}px)` },
      { clipPath: `circle(${Math.hypot(innerWidth, innerHeight)}px at ${partenza.x}px ${partenza.y}px)`, duration: 0.6, ease: "power3.inOut" }, 0.1)
    .fromTo(nuova, { x: partenza.x - arrivo.x, y: partenza.y - arrivo.y, scale: da ? da.width / nuova.offsetWidth : 0.8 },
      { x: 0, y: 0, scale: 1, duration: 0.7, ease: "power3.inOut" }, 0.1)
    .fromTo(nuova.querySelector(".anello path"), { strokeDashoffset: 1 }, { strokeDashoffset: 0, duration: 0.55, ease: "power2.inOut" }, 0.45)
    .fromTo(nuova.querySelector(".lettere"), { textShadow: "0 0 0px rgba(242,180,90,0)" },
      { textShadow: "0 0 28px rgba(242,180,90,.6)", duration: 0.5 }, 0.55)
    .from(v.querySelector(".luce"), { opacity: 0, scale: 0.6, duration: 0.8, ease: "power2.out" }, 0.4)
    .from(v.querySelector(".saluto > span"), { yPercent: 105, duration: 0.55, ease: CURVA }, 0.7)
    .to({}, { duration: 0.4 });
}

// Appena entrati in Cerca: il saluto si scioglie e la faccia vola dove resta, in alto
function benvenuto() {
  let dati = null;
  try { dati = JSON.parse(sessionStorage.getItem("cerca:benvenuto") || "null"); sessionStorage.removeItem("cerca:benvenuto"); } catch (e) { /* niente */ }
  if (!dati || FERMO || document.querySelector(".chi-guarda")) return;
  const v = htmlBenvenuto(dati.nome, dati.iniziali, dati.foto);
  v.style.backgroundColor = FONDO_SALOTTO;
  const faccia = v.querySelector(".faccia");
  const meta = [...document.querySelectorAll(".testata .faccia.piccola")].find((f) => f.getBoundingClientRect().width > 0);
  const tl = G.timeline({ onComplete: () => v.remove() });
  tl.to(v.querySelector(".saluto"), { opacity: 0, y: -8, duration: 0.3, ease: "power2.in" }, 0.2)
    .to(v.querySelector(".luce"), { opacity: 0, duration: 0.5 }, 0.2)
    .to(v, { backgroundColor: "rgba(22, 21, 19, 0)", duration: 0.55, ease: "power2.out" }, 0.35);
  if (meta) {
    const a = centro(faccia.getBoundingClientRect()), b = centro(meta.getBoundingClientRect());
    meta.style.visibility = "hidden";
    tl.to(faccia, { x: b.x - a.x, y: b.y - a.y, scale: meta.offsetWidth / faccia.offsetWidth, duration: 0.75, ease: "power3.inOut" }, 0.25)
      .to(faccia.querySelector(".anello"), { opacity: 0, duration: 0.3 }, 0.6)
      .add(() => { meta.style.visibility = ""; }, 1.0)
      .to(faccia, { opacity: 0, duration: 0.01 }, 1.0)
      .fromTo(meta, { scale: 1.35 }, { scale: 1, duration: 0.5, ease: "back.out(2.2)", clearProps: "transform" }, 1.0);
  } else {
    tl.to(faccia, { scale: 0.4, opacity: 0, duration: 0.45, ease: "power2.in" }, 0.3);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  benvenuto();
  chiudiVelo();
  disegnaLogo();
  indicatoreScorrevole(document.querySelector(".mondi"));
  indicatoreScorrevole(document.querySelector(".chip-vista"));
  entrataPagina();
  animaTutto(document);
});
