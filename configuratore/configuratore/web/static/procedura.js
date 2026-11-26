// Configurazione di mediabox dal browser: procedura guidata, avanzamento, accesso, impostazioni.
"use strict";

const BASE = document.body.dataset.base;
let STATO = JSON.parse(document.getElementById("stato").textContent);
const $ = (id) => document.getElementById(id);

function esc(t) {
  return String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

async function api(percorso, corpo) {
  const opzioni = corpo === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(corpo),
  };
  const r = await fetch(BASE + percorso, opzioni);
  let dati = {};
  try { dati = await r.json(); } catch (e) { /* risposta vuota */ }
  if (!r.ok) throw Object.assign(new Error(dati.errore || "Errore"), dati, { stato: r.status });
  return dati;
}

function avviso(testo) {
  const box = $("avviso");
  box.textContent = testo;
  box.hidden = false;
  clearTimeout(avviso.t);
  avviso.t = setTimeout(() => { box.hidden = true; }, 5000);
}

function messaggio(id, testo, tipo = "errore") {
  const el = $(id);
  if (!el) return;
  el.textContent = testo || "";
  el.className = "messaggio " + (testo ? tipo : "");
}

function seguo(valore) {
  try {
    if (valore === undefined) return sessionStorage.getItem("mediabox-seguo") === "si";
    if (valore) sessionStorage.setItem("mediabox-seguo", "si"); else sessionStorage.removeItem("mediabox-seguo");
  } catch (e) { /* senza memoria del browser: pazienza */ }
  return false;
}

function mostraVista(nome) {
  document.querySelectorAll(".vista").forEach((v) => { v.hidden = v.dataset.vista !== nome; });
  $("esci").hidden = !(STATO.connesso && nome === "impostazioni");
  if (nome === "impostazioni") {
    $("modulo").hidden = false;
    document.body.classList.add("in-impostazioni");
  }
}

// --- raccolta dei dati del modulo -------------------------------------------------------
function cartellaScelta() {
  const scelto = document.querySelector('input[name="disco"]:checked');
  if (!scelto) return "";
  return scelto.value === "altro" ? $("cartella-altra").value.trim() : scelto.value;
}

function datiModulo() {
  const voci = {};
  document.querySelectorAll("[data-voce]").forEach((c) => { voci[c.dataset.voce] = c.checked; });
  const vociWindows = {};
  document.querySelectorAll("[data-windows]").forEach((c) => { vociWindows[c.dataset.windows] = c.checked; });
  return {
    tmdb: $("tmdb") ? $("tmdb").value.trim() : "",
    tmdb_togli: Boolean($("tmdb-togli") && $("tmdb-togli").checked),
    password: $("password").value,
    password2: $("password2").value,
    voci, voci_windows: vociWindows,
    lingue: $("lingue").value,
    lingue_audio: $("lingue-audio").value,
    dimensione_max_gb: $("dimensione-max").value,
    telegram: { api_id: $("tg-api-id").value, api_hash: $("tg-api-hash").value, telefono: $("tg-telefono").value },
    ntfy: $("usa-ntfy").checked ? $("ntfy").value.trim() : "",
    utenti_casa: $("utenti-casa").value,
    cartella: cartellaScelta(),
  };
}

function aggiornaDipendenze(ev) {
  const bazarr = document.querySelector('[data-voce="WITH_BAZARR"]');
  $("blocco-lingue").hidden = bazarr && !bazarr.checked;
  $("blocco-ntfy").hidden = !$("usa-ntfy").checked;
  const tgarr = document.querySelector('[data-voce="WITH_TGARR"]');
  $("blocco-telegram").hidden = !tgarr || !tgarr.checked;
  const anime = document.querySelector('[data-voce="WITH_ANIME"]');
  const sonarr = document.querySelector('[data-voce="WITH_SONARR"]');
  if (anime && sonarr && anime.checked && !sonarr.checked) {
    sonarr.checked = true;
    messaggio("msg-scelte", "Gli anime passano da Sonarr: ho acceso anche le Serie TV.", "info");
  }
  // su un PC con poca memoria, chi accende un programma pesante viene avvisato
  const scelte = STATO.scelte || {};
  if (scelte.poca_memoria && ev && ev.target && ev.target.dataset && ev.target.dataset.memoria && ev.target.checked) {
    messaggio("msg-scelte", `Attenzione: questo PC ha ${String(scelte.memoria_gb).replace(".", ",")} GB di memoria e questo programma ne usa circa ` +
      `${ev.target.dataset.memoria} MB. Se tutto diventa lento, spegnilo di nuovo.`, "info");
  }
  const lunga = $("password").value.length >= 8;
  const caratteri = !/["\\]/.test($("password").value);
  document.querySelector('[data-regola="lunga"]').classList.toggle("ok", lunga);
  document.querySelector('[data-regola="caratteri"]').classList.toggle("ok", caratteri && $("password").value.length > 0);
}

// --- procedura a passi --------------------------------------------------------------------
// la chiave TMDB è facoltativa: nella procedura non la chiediamo, si mette dalle impostazioni
if (!STATO.configurato) document.querySelector('[data-passo="tmdb"]').remove();
const passi = [...document.querySelectorAll("#modulo .passo")].map((s) => s.dataset.passo);
let attuale = 0;
let tmdbVerificata = "";
let tmdbSenzaVerifica = false;

function mostraPasso(indice) {
  attuale = Math.max(0, Math.min(indice, passi.length - 1));
  document.querySelectorAll("#modulo .passo").forEach((s, i) => { s.hidden = i !== attuale; });
  $("indietro").hidden = attuale === 0;
  const ultimo = attuale === passi.length - 1;
  $("avanti").textContent = attuale === 0 ? "Inizia" : (ultimo ? "Installa mediabox" : "Avanti");
  $("avanti").classList.toggle("grande", ultimo);
  $("indicatore-testo").textContent = attuale === 0 ? "" : `Passo ${attuale} di ${passi.length - 1}`;
  $("tacche").innerHTML = passi.slice(1).map((_, i) => `<span class="${i < attuale ? "piena" : ""}"></span>`).join("");
  if (passi[attuale] === "riepilogo") riempiRiepilogo();
  const campo = { tmdb: $("tmdb"), password: $("password") }[passi[attuale]];
  if (campo && window.matchMedia("(min-width: 700px)").matches) campo.focus();
  window.scrollTo(0, 0);
}

async function controllaPasso() {
  const passo = passi[attuale];
  if (passo === "tmdb") {
    const chiave = $("tmdb").value.trim();
    if (!chiave) { messaggio("msg-tmdb", "Incolla la chiave di TMDB per andare avanti."); return false; }
    if (chiave === tmdbVerificata || tmdbSenzaVerifica) return true;
    messaggio("msg-tmdb", "Controllo la chiave…", "info");
    $("avanti").disabled = true;
    try {
      const r = await api("/api/verifica-tmdb", { chiave });
      if (r.ok) { tmdbVerificata = chiave; messaggio("msg-tmdb", "✔ " + r.messaggio, "ok"); return true; }
      messaggio("msg-tmdb", r.messaggio);
      if (r.sconosciuto) { tmdbSenzaVerifica = true; $("avanti").textContent = "Vai avanti lo stesso"; }
      return false;
    } catch (e) {
      messaggio("msg-tmdb", "Non riesco a controllare la chiave adesso. Premi di nuovo per andare avanti lo stesso.");
      tmdbSenzaVerifica = true;
      return false;
    } finally { $("avanti").disabled = false; }
  }
  if (passo === "password") {
    const p1 = $("password").value, p2 = $("password2").value;
    if (p1.length < 8) { messaggio("msg-password", "La password deve avere almeno 8 caratteri."); return false; }
    if (/["\\]/.test(p1)) { messaggio("msg-password", "Togli le virgolette doppie e la barra rovesciata."); return false; }
    if (p1 !== p2) { messaggio("msg-password", "Le due password non coincidono."); return false; }
    messaggio("msg-password", "");
  }
  if (passo === "cartella" && !cartellaScelta()) {
    messaggio("msg-cartella", "Scegli un disco oppure scrivi la cartella.");
    return false;
  }
  return true;
}

function riempiRiepilogo() {
  const d = datiModulo();
  const voci = [...document.querySelectorAll("[data-voce]")].filter((c) => c.checked)
    .map((c) => c.closest(".scelta").querySelector("strong").textContent);
  const righe = [
    ["Utente", "admin, con la password che hai scelto"],
    ["Attivi", ["Film", ...voci].join(", ")],
    ["Utenti di casa", d.utenti_casa.trim() || "Nessuno (puoi aggiungerli dopo)"],
    ["Film e musica in", d.cartella],
    ["Avvisi sul telefono", d.ntfy ? `ntfy, nome "${d.ntfy}"` : "No"],
  ];
  $("riepilogo").innerHTML = righe.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("");
}

$("modulo").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (document.body.classList.contains("in-impostazioni")) return salvaImpostazioni();
  if (!(await controllaPasso())) return;
  if (attuale < passi.length - 1) { mostraPasso(attuale + 1); return; }
  $("avanti").disabled = true;
  try {
    await api("/api/installa", datiModulo());
    seguo(true);
    STATO.configurato = true;
    avviaAvanzamento();
  } catch (e) {
    avviso(e.message);
    const indice = passi.indexOf({ tmdb: "tmdb", password: "password", password2: "password", lingue: "scelte", lingue_audio: "scelte", dimensione_max_gb: "scelte", telegram: "scelte", utenti_casa: "scelte", cartella: "cartella" }[e.campo]);
    if (indice >= 0) { mostraPasso(indice); messaggio(`msg-${passi[indice]}`, e.message); }
  } finally { $("avanti").disabled = false; }
});

$("indietro").addEventListener("click", () => mostraPasso(attuale - 1));
document.addEventListener("input", (ev) => {
  if (ev.target.id === "tmdb") { tmdbSenzaVerifica = false; messaggio("msg-tmdb", ""); }
  if (ev.target.id === "cartella-altra") $("disco-altro").checked = true;
  aggiornaDipendenze();
});
document.addEventListener("change", aggiornaDipendenze);
document.querySelectorAll("[data-mostra]").forEach((b) => b.addEventListener("click", () => {
  const campo = $(b.dataset.mostra);
  const visibile = campo.type === "text";
  campo.type = visibile ? "password" : "text";
  b.textContent = visibile ? "Mostra" : "Nascondi";
  $("password2").type = campo.type;
}));

$("prova-ntfy").addEventListener("click", async () => {
  const b = $("prova-ntfy");
  b.disabled = true;
  try {
    const r = await api("/api/prova-notifica", { argomento: $("ntfy").value });
    messaggio("msg-ntfy", "✔ " + r.messaggio, "ok");
  } catch (e) { messaggio("msg-ntfy", e.message); }
  b.disabled = false;
});

// --- avanzamento -----------------------------------------------------------------------------
let errori = 0;

function htmlPasso(p) {
  const icona = { fatto: "✔", in_corso: "", errore: "✘" }[p.stato] ?? "";
  return `<li class="${esc(p.stato)}"><span class="pallino" aria-hidden="true">${icona}</span>` +
    `<span>${esc(p.testo)}${p.stato === "in_corso" ? ' <span class="nascosto">(in corso)</span>' : ""}</span></li>`;
}

function htmlFine(a, ind, bene) {
  const problemi = (a.problemi || []).map((p) =>
    `<li><strong>${esc(p.nome ? p.nome + ": " : "")}${esc(p.cosa)}</strong>${p.cosa_fare ? `<span>${esc(p.cosa_fare)}</span>` : ""}</li>`).join("");
  const daFare = (a.da_fare || []).map((d) => `<li>${esc(d)}</li>`).join("");
  return (bene ? "" : `<div class="riquadro errore"><h2>Cosa non è andato</h2><ul class="problemi">${problemi ||
      "<li>Qualcosa si è fermato. Sul PC scrivi <code>mediabox configura</code> per riprovare.</li>"}</ul></div>`) +
    `<div class="azioni-fine"><a class="bottone grande" href="/">Apri mediabox</a>` +
    `<a class="bottone contorno" href="${esc(ind.pannello)}">Pannello</a></div>` +
    `<div class="riquadro"><h2>Indirizzi</h2><p>Da telefono, PC o TV di casa apri:</p><ul class="indirizzi">` +
    ind.tutti.map((u) => `<li><code>${esc(u)}</code></li>`).join("") + `</ul>` +
    `<p>Nell'app Jellyfin della TV scrivi come server <code>${esc(ind.jellyfin_tv)}</code><br>Utente <strong>admin</strong>, con la tua password.</p></div>` +
    (daFare ? `<div class="riquadro"><h2>Cosa resta da fare</h2><ol class="da-fare">${daFare}</ol></div>` : "") +
    (bene && problemi ? `<div class="riquadro errore"><h2>Da controllare</h2><ul class="problemi">${problemi}</ul></div>` : "");
}

function disegnaAvanzamento(s) {
  const a = s.applicazione;
  $("av-etichetta").textContent = STATO.configurato && !seguoPrimaInstallazione ? "Impostazioni" : "Installazione";
  $("av-passi").innerHTML = (a.passi || []).map(htmlPasso).join("") ||
    '<li class="in_corso"><span class="pallino" aria-hidden="true"></span><span>Preparo tutto…</span></li>';
  // una barra sola: prima i download, poi la configurazione dei programmi
  const sc = a.scarico, cf = a.configurazione;
  const barra = a.stato === "in_corso" && (sc || cf);
  $("av-scarico").hidden = !barra;
  if (barra) {
    const [n, di, testo] = sc ? [sc.n, sc.di, `Scarico ${sc.nome} (${sc.n} di ${sc.di})`]
      : [cf.n - 1, cf.di, `Configuro ${cf.nome} (${cf.n} di ${cf.di})`];
    $("av-barra").style.width = `${Math.max(3, Math.round((n / di) * 100))}%`;
    $("av-scarico-testo").textContent = testo;
  }
  $("av-ultima").textContent = a.stato === "in_corso" ? (a.ultima_riga || "") : "";
  const finito = a.stato === "fatto" || a.stato === "errore";
  $("av-fine").hidden = !finito;
  if (a.stato === "attesa") {
    $("av-titolo").textContent = "Un attimo…";
    $("av-frase").textContent = "Sto per cominciare.";
  } else if (a.stato === "in_corso") {
    $("av-titolo").textContent = seguoPrimaInstallazione ? "Sto installando mediabox" : "Applico le modifiche";
    $("av-frase").textContent = "Puoi lasciare aperta questa pagina, ma non serve: continua anche se la chiudi.";
  } else if (finito) {
    const bene = a.stato === "fatto" && !(a.problemi || []).length;
    const titoloBene = seguoPrimaInstallazione ? "mediabox è pronto" : "Modifiche applicate";
    $("av-titolo").textContent = a.stato === "fatto" ? titoloBene : "Quasi fatto, ma qualcosa non va";
    $("av-frase").textContent = a.stato === "fatto"
      ? (bene ? (seguoPrimaInstallazione ? "Tutto configurato. Buona visione!" : "mediabox usa già le nuove scelte.")
        : "È tutto acceso, ma c'è qualcosa da controllare.")
      : "Leggi qui sotto cosa è successo.";
    $("av-fine").innerHTML = htmlFine(a, s.indirizzi, a.stato === "fatto") +
      (seguoPrimaInstallazione ? "" : `<div class="azioni-fine"><button type="button" class="contorno" id="torna">Torna alle impostazioni</button></div>`);
    const torna = $("torna");
    if (torna) torna.addEventListener("click", () => { seguo(false); location.reload(); });
  }
  return finito;
}

let seguoPrimaInstallazione = !STATO.connesso || !STATO.configurato;

async function avviaAvanzamento() {
  mostraVista("avanzamento");
  window.scrollTo(0, 0);
  let s = STATO;
  for (;;) {
    try {
      s = await api("/api/stato");
      errori = 0;
      if (disegnaAvanzamento(s)) { seguo(false); return; }
    } catch (e) {
      // durante il passaggio alla versione definitiva la pagina non risponde per qualche secondo
      errori += 1;
      if (errori > 3) $("av-ultima").textContent = "Riavvio i servizi: la pagina torna da sola tra poco.";
    }
    await new Promise((r) => setTimeout(r, 2000));
  }
}

// --- impostazioni dopo l'installazione ---------------------------------------------------------
// "Salva" si accende solo se qualcosa è cambiato davvero: senza modifiche non si salva e non si rifà niente
let moduloIniziale = "";

function controllaModifiche() {
  const cambiato = JSON.stringify(datiModulo()) !== moduloIniziale;
  $("salva").disabled = !cambiato;
  $("salva").textContent = cambiato ? "Salva e applica" : "Nessuna modifica";
}

function seguiModifiche() {
  moduloIniziale = JSON.stringify(datiModulo());
  ["input", "change"].forEach((tipo) => $("modulo").addEventListener(tipo, controllaModifiche));
  controllaModifiche();
}

async function salvaImpostazioni() {
  if (JSON.stringify(datiModulo()) === moduloIniziale) { avviso("Non hai cambiato nulla."); return; }
  $("salva").disabled = true;
  const chiave = $("tmdb").value.trim();
  if (chiave && chiave !== tmdbVerificata) {
    // una chiave nuova si controlla prima di salvarla
    try {
      const r = await api("/api/verifica-tmdb", { chiave });
      if (!r.ok && !r.sconosciuto) {
        messaggio("msg-tmdb", r.messaggio);
        $("tmdb").scrollIntoView({ block: "center" });
        $("salva").disabled = false;
        return;
      }
      tmdbVerificata = chiave;
    } catch (e) { /* se non si può controllare, la salviamo lo stesso */ }
  }
  try {
    const r = await api("/api/impostazioni", datiModulo());
    if (!r.cambiato) { avviso(r.messaggio); return; }
    if (r.cosa) avviso(r.cosa);
    seguo(true);
    seguoPrimaInstallazione = false;
    avviaAvanzamento();
  } catch (e) {
    if (e.stato === 401) { location.reload(); return; }
    avviso(e.message);
    if (e.campo) messaggio(`msg-${{ lingue: "scelte", utenti_casa: "scelte", lingue_audio: "scelte", dimensione_max_gb: "scelte", telegram: "scelte", cartella: "cartella" }[e.campo] || e.campo}`, e.message);
  } finally { controllaModifiche(); }
}

// --- aggiornamenti ------------------------------------------------------------------------------------
const FASI_IN_CORSO = ["in_attesa", "controllo", "scarico", "installo"];

function testoVersione(a) {
  let testo = `Versione installata: ${a.versione}`;
  if (a.fonte === "main" && a.id) testo += ` (${a.id.slice(0, 7)}${a.data ? ", " + a.data.split("-").reverse().join("/") : ""})`;
  if (a.fonte === "release" && a.id) testo += ` (${a.id})`;
  if (!a.id) testo += " (scaricata a mano)";
  return testo;
}

function disegnaAggiornamento(d) {
  $("agg-versione").textContent = testoVersione(d.attuale);
  $("agg-automatici").textContent = d.automatici
    ? "Si aggiorna da solo ogni notte verso le 4:30: scarica e installa solo le parti cambiate."
    : "Gli aggiornamenti automatici sono spenti (li accendi qui sopra, in Manutenzione).";
  const registro = $("agg-registro");
  registro.textContent = (d.registro || []).join("\n");
  registro.hidden = !(d.in_corso || d.stato.fase === "errore") || !d.registro.length;
  if (d.in_corso) {
    messaggio("agg-stato", d.stato.messaggio || "Aggiornamento in corso…", "ok");
  } else if (d.stato.fase === "errore") {
    messaggio("agg-stato", d.stato.messaggio || "L'ultimo aggiornamento non è riuscito.");
  }
  $("agg-verifica").disabled = $("agg-installa").disabled = d.in_corso;
}

async function seguiAggiornamento() {
  let senzaRisposta = 0;
  let partito = Date.now();
  for (;;) {
    await new Promise((r) => setTimeout(r, 3000));
    let d;
    try {
      d = await api("/api/aggiornamento");
      senzaRisposta = 0;
    } catch (e) {
      // durante l'aggiornamento questa pagina riparte: è normale che per un po' non risponda
      senzaRisposta += 1;
      messaggio("agg-stato", "I programmi stanno ripartendo: la pagina torna da sola tra poco…", "ok");
      continue;
    }
    disegnaAggiornamento(d);
    if (d.stato.fase === "in_attesa" && Date.now() - partito > 90000) {
      messaggio("agg-stato", "L'aggiornamento non è partito. Sul PC di mediabox scrivi: mediabox update");
      $("agg-verifica").disabled = $("agg-installa").disabled = false;
      return;
    }
    if (d.stato.fase === "fatto") {
      messaggio("agg-stato", d.stato.messaggio + " Ricarico la pagina…", "ok");
      setTimeout(() => location.reload(), 3000);
      return;
    }
    if (d.stato.fase === "aggiornato") {
      messaggio("agg-stato", "Hai già l'ultima versione.", "ok");
      $("agg-installa").hidden = true;
      return;
    }
    if (d.stato.fase === "errore") return;  // il messaggio è già sulla pagina
    if (!d.in_corso) {
      // finito senza dirci come: ricarichiamo, la pagina mostrerà la versione installata
      messaggio("agg-stato", "Aggiornamento finito. Ricarico la pagina…", "ok");
      setTimeout(() => location.reload(), 3000);
      return;
    }
    if (d.stato.fase !== "in_attesa") partito = Date.now();
  }
}

async function avviaAggiornamenti() {
  if (!$("blocco-aggiornamenti")) return;
  try {
    const d = await api("/api/aggiornamento");
    disegnaAggiornamento(d);
    if (d.in_corso) seguiAggiornamento();
  } catch (e) { $("blocco-aggiornamenti").hidden = true; }

  $("agg-verifica").addEventListener("click", async () => {
    $("agg-verifica").disabled = true;
    messaggio("agg-stato", "Controllo su GitHub…", "ok");
    try {
      const r = await api("/api/aggiornamento/controlla", {});
      if (r.errore) { messaggio("agg-stato", r.errore); return; }
      if (!r.disponibile) {
        messaggio("agg-stato", "Hai già l'ultima versione.", "ok");
        $("agg-installa").hidden = true;
        return;
      }
      const u = r.ultima;
      const cosa = u.fonte === "release" ? `la versione ${u.versione}` : "una versione nuova";
      const quando = u.data ? ` del ${u.data.split("-").reverse().join("/")}` : "";
      messaggio("agg-stato", `C'è ${cosa}${quando}${u.messaggio ? ": " + u.messaggio : ""}.`, "ok");
      $("agg-installa").hidden = false;
    } catch (e) {
      if (e.stato === 401) { location.reload(); return; }
      messaggio("agg-stato", e.message);
    } finally { $("agg-verifica").disabled = false; }
  });

  $("agg-installa").addEventListener("click", async () => {
    if (!confirm("Aggiorno mediabox adesso? Per qualche minuto alcuni programmi potrebbero non rispondere.")) return;
    $("agg-installa").disabled = $("agg-verifica").disabled = true;
    try {
      const r = await api("/api/aggiornamento/installa", {});
      messaggio("agg-stato", r.messaggio || "Aggiornamento richiesto: parte tra pochi secondi…", "ok");
      seguiAggiornamento();
    } catch (e) {
      if (e.stato === 401) { location.reload(); return; }
      messaggio("agg-stato", e.message);
      $("agg-installa").disabled = $("agg-verifica").disabled = false;
    }
  });
}

// --- fuori casa (Tailscale) ---------------------------------------------------------------------
// Tailscale si installa e si aggiorna da solo con mediabox: qui serve solo accedere, una volta.
async function avviaFuoriCasa() {
  if (!$("blocco-fuori-casa")) return;
  const testo = $("fc-testo");
  let d;
  try { d = await api("/api/tailscale"); } catch (e) { $("blocco-fuori-casa").hidden = true; return; }
  $("fc-link").hidden = $("fc-apri").hidden = $("fc-passi").hidden = true;
  $("fc-stato").textContent = "";
  if (d.stato === "collegato") {
    testo.textContent = `Attivo${d.account ? " con l'account " + d.account : ""}. Da fuori casa mediabox è qui: ${d.indirizzo}`;
    $("fc-apri").href = d.indirizzo;
    $("fc-apri").hidden = $("fc-passi").hidden = false;
    if (d.scade) {
      messaggio("fc-stato", `L'accesso scade il ${d.scade.split("-").reverse().join("/")}: su login.tailscale.com, ` +
        `in Machines, scegli "Disable key expiry" per mediabox, così non scade più.`, "ok");
    }
    return;
  }
  if (d.stato === "da_collegare") {
    testo.textContent = "Per usare mediabox fuori casa accedi a Tailscale (gratis) con il tuo account Google, " +
      "Microsoft o Apple, o con la tua email. Basta una volta.";
    $("fc-link").href = d.link;
    $("fc-link").hidden = false;
  } else if (d.stato === "windows") {
    testo.textContent = "Su Windows: sul PC di mediabox scrivi \"mediabox remote\" in PowerShell, poi accedi dall'icona di Tailscale.";
    return;
  } else if (d.stato === "avvio") {
    testo.textContent = "Tailscale si sta avviando…";
  } else {
    testo.textContent = "Tailscale non è ancora attivo: parte con il prossimo aggiornamento di mediabox " +
      "(o subito con \"mediabox configura\" sul PC).";
    return;
  }
  setTimeout(avviaFuoriCasa, 4000);  // finché non hai fatto l'accesso, la pagina si aggiorna da sola
}

$("modulo-accesso").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    await api("/api/accedi", { password: $("password-accesso").value });
    location.reload();
  } catch (e) { messaggio("msg-accesso", e.message); }
});

$("esci").addEventListener("click", async () => { await api("/api/esci", {}); location.reload(); });

// --- avvio ------------------------------------------------------------------------------------
(function avvia() {
  const a = STATO.applicazione;
  aggiornaDipendenze();
  if (a.stato === "attesa" || a.stato === "in_corso" || (seguo() && (a.stato === "fatto" || a.stato === "errore"))) {
    seguoPrimaInstallazione = !STATO.connesso;
    avviaAvanzamento();
  } else if (!STATO.configurato) {
    mostraVista("procedura");
    mostraPasso(0);
  } else if (STATO.connesso) {
    document.querySelectorAll("#modulo .passo").forEach((s) => {
      s.hidden = ["benvenuto", "password", "riepilogo"].includes(s.dataset.passo);
    });
    mostraVista("impostazioni");
    seguiModifiche();
    avviaAggiornamenti();
    avviaFuoriCasa();
  } else {
    mostraVista("accesso");
  }
})();
