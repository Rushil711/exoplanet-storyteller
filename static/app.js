"use strict";

const FEATURED = ["TRAPPIST-1 e", "55 Cnc e", "Kepler-452 b", "Proxima Cen b", "HD 209458 b", "Kepler-785 b"];
const sessionId = localStorage.getItem("es-session") || crypto.randomUUID();
localStorage.setItem("es-session", sessionId);

const state = {
  status: null,
  kid: localStorage.getItem("es-kid") === "1",
  model: localStorage.getItem("es-model") || "",
  story: null,
  image: null,
  audio: null,
  compare: null,
  compareOn: false,
  busy: false,
  error: null,
  suggestions: [],
  sugIndex: -1,
  search: null,
};

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[c]));

async function api(url, opts) {
  const r = await fetch(url, opts);
  let data = null;
  try { data = await r.json(); } catch { data = null; }
  if (!r.ok) {
    const detail = data && (data.error || data.detail);
    const msg = typeof detail === "string" ? detail : (detail ? "The request was rejected." : r.statusText);
    const err = new Error(msg || "Request failed");
    err.status = r.status;
    throw err;
  }
  return data;
}

function post(url, body) {
  return api(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

function toast(msg) {
  const el = $("toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove("show"), 4200);
}

function fmt(v) {
  if (v === null || v === undefined || v === "") return "unknown";
  if (typeof v !== "number") return String(v);
  if (Number.isInteger(v)) return String(v);
  // Keep the archive digits. Rounding here made 0.7365474 look like 0.737.
  const rounded = Math.round(v * 1e8) / 1e8;
  return String(rounded);
}

function tip(c) {
  if (c.status === "exact" || c.status === "rounded") {
    return `${c.field} = ${fmt(c.data_value)} · ${c.status}`;
  }
  if (c.status === "derived") return `Unit conversion of ${c.field}. Stories are asked to keep the archive unit.`;
  if (c.status === "hedged") return "Stated as unknown, which the archive allows.";
  if (c.status === "text") return "Digits from the name, not a measurement.";
  return c.via || "Not supported by the archive record.";
}

function renderStory(text, claims) {
  const marks = (claims || [])
    .filter((c) => Number.isInteger(c.start) && c.end > c.start && c.status !== "text")
    .sort((a, b) => a.start - b.start);
  let html = "";
  let i = 0;
  for (const c of marks) {
    if (c.start < i) continue;
    html += esc(text.slice(i, c.start)).replace(/\n/g, "<br>");
    const field = c.field || "";
    html += `<button type="button" class="tok ${esc(c.status)}" data-field="${esc(field)}" title="${esc(tip(c))}">${esc(text.slice(c.start, c.end))}</button>`;
    i = c.end;
  }
  html += esc(text.slice(i)).replace(/\n/g, "<br>");
  return html;
}

function claimList(claims) {
  const rows = (claims || []).filter((c) => c.status !== "text");
  if (!rows.length) return "<p class='quiet'>No checkable claims were found.</p>";
  return `<ul class="claims">${rows.map((c) => {
    const cls = c.status === "UNSUPPORTED" ? "bad" : (c.status === "derived" ? "mid" : "ok");
    const mark = c.status === "UNSUPPORTED" ? "✕" : (c.status === "derived" ? "~" : "✓");
    const fb = (c.status === "UNSUPPORTED" || c.status === "derived") && c.via
      ? "" : "";
    return `<li class="${cls}"><span class="mark">${mark}</span><span><b>${esc(c.text)}</b> · ${esc(c.status)}</span><span class="why">${esc(tip(c))}</span></li>`;
  }).join("")}</ul>`;
}

function attemptsHtml(s) {
  const items = (s.attempts || []).map((a) => {
    const cls = a.passed ? "pass" : "fail";
    const bits = a.error ? a.error : `score ${a.score} · ${a.unsupported} unsupported number${a.unsupported === 1 ? "" : "s"}`;
    const fb = !a.passed && a.feedback && a.feedback.length
      ? `<details><summary>Verifier feedback sent back</summary><ul>${a.feedback.map((f) => `<li>${esc(f)}</li>`).join("")}</ul></details>`
      : "";
    return `<li class="${cls}"><span>${a.n}</span> Attempt ${a.n} · ${esc(bits)}${fb}</li>`;
  });
  if (s.origin === "template") {
    items.push(`<li class="pass"><span>✓</span>Safe template, written from the record itself</li>`);
  }
  return `<ol class="attempts">${items.join("")}</ol>`;
}

function factsTable(facts) {
  return `<table class="facts"><tbody>${facts.map((f) => {
    const unknown = f.value === null || f.value === undefined;
    return `<tr id="fact-${esc(f.field)}" class="${unknown ? "unknown" : ""}"><th>${esc(f.label)}</th><td>${esc(fmt(f.value))}${f.unit && !unknown ? " " + esc(f.unit) : ""}</td></tr>`;
  }).join("")}</tbody></table>`;
}

function scoreTone(score, passed) {
  if (!passed) return "var(--bad)";
  if (score >= 100) return "var(--ok)";
  return "var(--mid)";
}

function renderStage() {
  const stage = $("stage");
  if (state.busy && !state.story) {
    const offline = state.status && state.status.llm.backend === "offline";
    stage.innerHTML = `<section class="loading">
      <p class="eyebrow">Checking the archive</p>
      <h1>Writing ${esc(state.busyName || "this world")}…</h1>
      <div class="pulse"></div>
      <p class="lede">${offline
        ? "Replaying a recorded Gemma story, then checking every claim against the record. This is quick."
        : "Gemma is writing on this machine. On a CPU that is often 10–30 seconds, then the verifier reads it back."}</p>
    </section>`;
    return;
  }
  if (!state.story) {
    stage.innerHTML = `<section class="empty">
      <p class="eyebrow">A planetarium desk for real worlds</p>
      <h1>Pick a planet. Hear only what the archive can stand behind.</h1>
      <p class="lede">Gemma, running locally, writes a short story from one planet’s NASA record. A verifier then checks every number, unit, spectral type and claim. If the story can’t be grounded, you get one built straight from the data instead — and you can see the score either way.</p>
      ${state.error ? `<p class="banner error">${esc(state.error)}</p>` : ""}
      <div class="featured">${FEATURED.map((n) => `<button type="button" data-planet="${esc(n)}">${esc(n)}</button>`).join("")}</div>
    </section>`;
    return;
  }
  const s = state.story;
  const v = s.verification;
  const p = s.planet;
  const tone = scoreTone(v.score, v.passed);
  const meta = [
    p.hostname && `orbits ${p.hostname}`,
    p.disc_year && `found ${p.disc_year}`,
    p.discoverymethod,
    p.sy_dist != null && `${fmt(p.sy_dist)} parsecs`,
  ].filter(Boolean);
  const art = state.image
    ? `<img alt="Artwork of ${esc(p.pl_name)} generated from its archive record" src="${esc(state.image.url)}">
       <div class="cap"><div class="badge">${esc(state.image.mode === "mock" ? "Mock render" : "Grok Imagine")}</div>
       <p>${esc(state.image.label || "")}</p>
       <details><summary>Why the picture looks like this</summary><ul>
         ${(state.image.derivation || []).map((d) => `<li><b>${esc(d.aspect)}.</b> ${esc(d.value)} <span class="quiet">(${esc(d.field)})</span></li>`).join("")}
       </ul></details></div>`
    : `<div class="placeholder">${state.imageError ? esc(state.imageError) : "Painting this world from its temperature, size and orbit…"}</div>`;
  const audioHint = state.audio
    ? (state.audio.mode === "mock"
      ? "Mock mode: the player is a short placeholder chime, not a voice. Listen uses your browser’s own speech. With XAI_API_KEY set, this becomes the xAI voice."
      : state.audio.label)
    : (state.audioError || "Preparing narration…");
  const highlights = (s.highlights || []).map((h) => `<li>${esc(h.text)}</li>`).join("");
  stage.innerHTML = `
    <header class="planet-head">
      <div>
        <p class="eyebrow">${esc(s.record_source)} · ${esc(s.llm.label || s.llm.backend)}${s.kid ? " · kid-friendly" : ""}</p>
        <ul class="meta">${meta.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>
        <h1>${esc(p.pl_name)}</h1>
      </div>
      <div class="stage-tools">
        <button type="button" id="compare-btn" aria-pressed="${state.compareOn}">Earth versus this world</button>
      </div>
    </header>
    ${state.error ? `<p class="banner error">${esc(state.error)}</p>` : ""}
    ${s.fallback_reason ? `<p class="banner">${esc(s.fallback_reason)}</p>` : ""}
    ${s.llm.note ? `<p class="banner">${esc(s.llm.note)}</p>` : ""}
    ${highlights ? `<ul class="whats-new">${highlights}</ul>` : ""}
    <div class="columns">
      <div>
        <p class="story">${renderStory(s.story, v.claims)}</p>
        <div class="audio">
          <button type="button" id="listen">Listen</button>
          ${state.audio ? `<audio controls src="${esc(state.audio.url)}"></audio>` : ""}
          <p class="hint">${esc(audioHint)}</p>
        </div>
      </div>
      <figure class="art">${art}</figure>
    </div>
    <div class="lower">
      <section class="panel" aria-label="Grounding">
        <h2>Grounding</h2>
        <div class="score-row">
          <div class="ring" style="--p:${v.score}; --tone:${tone}"><b>${v.score}</b></div>
          <p class="score-copy">${v.passed
            ? "Every checkable claim matches the archive, in the archive’s own units."
            : "Something in this text is not supported. Unsupported claims score zero; unit conversions score half."}</p>
        </div>
        ${attemptsHtml(s)}
        <h2>Claim by claim</h2>
        ${claimList(v.claims)}
      </section>
      <section class="panel" aria-label="Archive record">
        <h2>The record behind the story</h2>
        <p class="quiet">Click a highlighted word in the story to land on its field.</p>
        ${factsTable(s.facts)}
      </section>
    </div>
    <section class="panel compare-wrap" id="compare" ${state.compareOn ? "" : "hidden"}>
      <h2>Earth versus ${esc(p.pl_name)}</h2>
      <div id="compare-body">${state.compare ? "" : "Loading the comparison…"}</div>
    </section>`;
  if (state.compareOn) fillCompare();
}

function fillCompare() {
  const host = $("compare-body");
  if (!host || !state.compare) return;
  const rows = state.compare.rows.map((r) => {
    let bar = "";
    if (r.ratio && r.ratio > 0) {
      const mag = Math.max(-2, Math.min(2, Math.log10(r.ratio)));
      bar = `<span class="bar" title="log scale"><i style="width:${((mag + 2) / 4) * 100}%"></i></span>`;
    }
    return `<tr><th>${esc(r.label)}</th><td>${esc(fmt(r.planet))}${r.planet != null ? " " + esc(r.unit) : ""}</td><td>${esc(fmt(r.earth))} ${esc(r.unit)}</td><td>${bar}${esc(r.summary)}</td></tr>`;
  }).join("");
  const dist = state.compare.distance || {};
  host.innerHTML = `<table class="compare"><thead><tr><th></th><th>This world</th><th>${esc(state.compare.rows[0] ? "Reference" : "")}</th><th></th></tr></thead><tbody>${rows}</tbody></table>
    <p class="src">${dist.light_travel_years != null ? `Starlight from here takes about ${esc(fmt(dist.light_travel_years))} years to reach Earth (parsecs × 3.26, computed for this table only — stories are not allowed to convert units). ` : ""}${esc(state.compare.sources || "")}</p>`;
}

function renderSession(items) {
  const ul = $("session");
  if (!items.length) {
    ul.innerHTML = `<li class="quiet">Nothing yet. The first world sets the baseline.</li>`;
    return;
  }
  ul.innerHTML = items.map((i) => `<li><button type="button" data-planet="${esc(i.pl_name)}">${esc(i.pl_name)}<small>score ${esc(i.score)} · ${esc(i.origin)}</small></button></li>`).join("");
}

function renderSearch() {
  const box = $("interpreted");
  const list = $("results");
  const s = state.search;
  if (!s) { box.hidden = true; list.innerHTML = ""; return; }
  box.hidden = false;
  if (!s.understood) {
    box.innerHTML = `<p>${esc(s.message)}</p><p class="quiet">${(s.examples || []).map(esc).join(" · ")}</p>`;
    list.innerHTML = "";
    return;
  }
  const bits = (s.description || []).map((d) => `<li>${esc(d)}</li>`).join("");
  const notes = (s.notes || []).map((n) => `<p class="note">${esc(n)}</p>`).join("");
  box.innerHTML = `<p>Mapped to archive fields (${esc(s.parser)}), not to free-form query text.</p><ul>${bits}</ul>${notes}
    <p class="quiet">${esc(s.source || "")}${s.warning ? " · " + esc(s.warning) : ""}</p>
    <code>${esc(s.adql || "")}</code>`;
  list.innerHTML = (s.results || []).map((r) => `<li><button type="button" data-planet="${esc(r.pl_name)}">${esc(r.pl_name)}<small>${esc(r.discoverymethod || "")} · ${esc(r.disc_year || "")}${r.pl_eqt != null ? " · " + esc(fmt(r.pl_eqt)) + " K" : ""}</small></button></li>`).join("");
  if (!s.results || !s.results.length) list.innerHTML = `<li class="quiet">No confirmed planet matched.</li>`;
}

async function refreshSession() {
  try {
    const data = await api("/api/session?session_id=" + encodeURIComponent(sessionId));
    renderSession(data.told || []);
  } catch { /* the list is a convenience */ }
}

async function tell(name) {
  name = (name || "").trim();
  if (!name || state.busy) return;
  state.busy = true;
  state.error = null;
  state.story = null;
  state.image = null;
  state.audio = null;
  state.compare = null;
  state.imageError = null;
  state.audioError = null;
  state.busyName = name;
  $("q").value = name;
  closeSuggest();
  renderStage();
  try {
    const story = await post("/api/story", { name, session_id: sessionId, kid: state.kid, model: state.model || null });
    state.story = story;
    state.busy = false;
    renderStage();
    refreshSession();
    loadMedia(story.planet.pl_name);
    if (state.compareOn) loadCompare(story.planet.pl_name);
  } catch (e) {
    state.busy = false;
    state.error = e.message;
    renderStage();
  }
}

async function loadMedia(name) {
  const [img, aud] = await Promise.allSettled([
    post("/api/image", { name }),
    post("/api/narrate", { name, session_id: sessionId }),
  ]);
  if (img.status === "fulfilled") state.image = img.value;
  else state.imageError = img.reason.message;
  if (aud.status === "fulfilled") state.audio = aud.value;
  else state.audioError = aud.reason.message;
  if (state.story && state.story.planet.pl_name === name) renderStage();
}

async function loadCompare(name) {
  try {
    state.compare = await api("/api/compare?name=" + encodeURIComponent(name));
  } catch (e) {
    state.compare = { rows: [], sources: e.message, distance: {} };
  }
  if (state.compareOn) fillCompare();
}

function closeSuggest() {
  $("suggest").hidden = true;
  $("q").setAttribute("aria-expanded", "false");
  state.sugIndex = -1;
}

function renderSuggest() {
  const ul = $("suggest");
  if (!state.suggestions.length) { closeSuggest(); return; }
  ul.hidden = false;
  $("q").setAttribute("aria-expanded", "true");
  ul.innerHTML = state.suggestions.map((s, i) => `<li role="option" id="sug-${i}" aria-selected="${i === state.sugIndex}"><button type="button" data-planet="${esc(s.pl_name)}">${esc(s.pl_name)}<small>${esc(s.hostname || "")} · ${esc(s.disc_year || "")}</small></button></li>`).join("");
}

let sugTimer = 0;
$("q").addEventListener("input", () => {
  clearTimeout(sugTimer);
  const q = $("q").value.trim();
  if (q.length < 2) { state.suggestions = []; renderSuggest(); return; }
  sugTimer = setTimeout(async () => {
    try {
      const data = await api("/api/suggest?q=" + encodeURIComponent(q));
      state.suggestions = data.results || [];
      state.sugIndex = state.suggestions.length ? 0 : -1;
      renderSuggest();
    } catch { closeSuggest(); }
  }, 120);
});

$("q").addEventListener("keydown", (e) => {
  const n = state.suggestions.length;
  if (e.key === "ArrowDown" && n) {
    e.preventDefault();
    state.sugIndex = (state.sugIndex + 1) % n;
    renderSuggest();
  } else if (e.key === "ArrowUp" && n) {
    e.preventDefault();
    state.sugIndex = (state.sugIndex - 1 + n) % n;
    renderSuggest();
  } else if (e.key === "Enter") {
    e.preventDefault();
    const pick = state.sugIndex >= 0 ? state.suggestions[state.sugIndex] : null;
    tell(pick ? pick.pl_name : $("q").value);
  } else if (e.key === "Escape") closeSuggest();
});

$("pick").addEventListener("submit", (e) => e.preventDefault());
document.addEventListener("click", (e) => {
  if (!e.target.closest(".combo")) closeSuggest();
  const planet = e.target.closest("[data-planet]");
  if (planet) tell(planet.dataset.planet);
});

$("ask").addEventListener("submit", async (e) => {
  e.preventDefault();
  const query = $("nl").value.trim();
  if (!query) return;
  $("ask-btn").disabled = true;
  try {
    state.search = await post("/api/search", { query });
    renderSearch();
  } catch (err) {
    toast(err.message);
  } finally {
    $("ask-btn").disabled = false;
  }
});

$("today").addEventListener("click", async () => {
  $("today").disabled = true;
  try {
    const card = await api("/api/today");
    tell(card.pl_name);
  } catch (err) {
    toast(err.message);
  } finally {
    $("today").disabled = false;
  }
});

$("kid").checked = state.kid;
$("kid").addEventListener("change", () => {
  state.kid = $("kid").checked;
  localStorage.setItem("es-kid", state.kid ? "1" : "0");
  toast(state.kid ? "Kid-friendly mode on. The next story uses shorter sentences." : "Kid-friendly mode off.");
});

$("model").addEventListener("change", () => {
  state.model = $("model").value;
  localStorage.setItem("es-model", state.model);
});

$("stage").addEventListener("click", async (e) => {
  const tok = e.target.closest(".tok");
  if (tok && tok.dataset.field) {
    const row = $("fact-" + tok.dataset.field);
    if (row) {
      row.scrollIntoView({ block: "center", behavior: "smooth" });
      row.classList.add("flash");
      setTimeout(() => row.classList.remove("flash"), 1600);
    }
    return;
  }
  if (e.target.id === "listen") {
    const text = state.story && state.story.story;
    if (!text || !window.speechSynthesis) { toast("This browser has no speech synthesis."); return; }
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "en";
    u.rate = state.kid ? 0.92 : 1;
    window.speechSynthesis.speak(u);
    return;
  }
  if (e.target.id === "compare-btn") {
    state.compareOn = !state.compareOn;
    e.target.setAttribute("aria-pressed", String(state.compareOn));
    const panel = $("compare");
    if (panel) panel.hidden = !state.compareOn;
    if (state.compareOn && state.story && !state.compare) await loadCompare(state.story.planet.pl_name);
    else if (state.compareOn) fillCompare();
  }
});

function drawSky() {
  const c = $("sky");
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  c.width = Math.floor(innerWidth * dpr);
  c.height = Math.floor(innerHeight * dpr);
  const g = c.getContext("2d");
  g.scale(dpr, dpr);
  let seed = 20261025;
  const rnd = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };
  const n = Math.round(Math.min(220, innerWidth / 7));
  for (let i = 0; i < n; i++) {
    g.globalAlpha = 0.15 + rnd() * 0.75;
    g.fillStyle = rnd() > 0.88 ? "#f0d7a2" : "#d7e2f4";
    g.beginPath();
    g.arc(rnd() * innerWidth, rnd() * innerHeight, rnd() * 1.3, 0, 7);
    g.fill();
  }
}

async function boot() {
  drawSky();
  window.addEventListener("resize", drawSky);
  try {
    state.status = await api("/api/status");
  } catch (e) {
    state.error = "The app server is not responding.";
    renderStage();
    return;
  }
  const s = state.status;
  const llm = s.llm.backend === "offline" ? "Offline Gemma replay" : s.llm.label;
  const media = s.media.mode === "mock" ? "Mock art & voice" : "xAI Imagine & Voice";
  $("pills").innerHTML = `
    <span class="pill"><strong>${esc(llm)}</strong></span>
    <span class="pill ${s.media.mode === "mock" ? "warn" : ""}">${esc(media)}</span>
    <span class="pill">${(s.archive.catalog_rows || 0).toLocaleString()} planets</span>`;
  const models = s.llm.models || [];
  $("model").innerHTML = models.map((m) => `<option value="${esc(m)}">${esc(m)}</option>`).join("");
  if (state.model && models.includes(state.model)) $("model").value = state.model;
  else state.model = $("model").value || "";
  const nl = $("nl");
  if (s.examples && s.examples.length) nl.placeholder = s.examples[0];
  if (!s.llm.ok && s.llm.detail) toast(s.llm.detail);
  refreshSession();
}

boot();
