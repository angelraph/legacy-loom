// Legacy Loom front end. Plain JS, no build step.

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (sec) => { sec = Math.max(0, Math.floor(sec || 0)); return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`; };
const STAGES = { queued: "Waiting", transcribing: "Listening", reading: "Gemma is reading", indexing: "Filing it away", error: "Failed" };

const state = { family: {}, memos: [], history: [], memoCache: {}, statusData: null, pr: { sub: "she", obj: "her", pos: "her" },
  space: "main", canWrite: false };
const PRONOUNS = { she: { sub: "she", obj: "her", pos: "her" }, he: { sub: "he", obj: "him", pos: "his" }, they: { sub: "they", obj: "them", pos: "their" } };

function passKey() { try { return localStorage.getItem("ll-pass") || ""; } catch { return ""; } }
function setPassKey(v) { try { localStorage.setItem("ll-pass", v); } catch {} }

async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (passKey()) headers["x-family-key"] = passKey();
  headers["x-space"] = state.space;
  if (opts.json !== undefined) { headers["content-type"] = "application/json"; opts.body = JSON.stringify(opts.json); }
  const res = await fetch(path, { ...opts, headers });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  const type = res.headers.get("content-type") || "";
  return type.includes("json") ? res.json() : res;
}

function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toast.timer); toast.timer = setTimeout(() => t.classList.remove("show"), 3200);
}

// Spaces: the friend's archive, or an open sandbox anyone can try

function initialSpace() {
  const fromUrl = new URLSearchParams(location.search).get("space");
  if (fromUrl === "try" || fromUrl === "main") return fromUrl;
  try { return localStorage.getItem("ll-space") === "try" ? "try" : "main"; } catch { return "main"; }
}

function applySpace() {
  $$("#spaceBar [data-space]").forEach((b) => b.classList.toggle("on", b.dataset.space === state.space));
  document.body.classList.toggle("space-try", state.space === "try");
  $$(".book-link").forEach((a) => { a.href = state.space === "try" ? "/book?space=try" : "/book"; });
  const url = new URL(location.href);
  if (state.space === "try") url.searchParams.set("space", "try"); else url.searchParams.delete("space");
  history.replaceState(null, "", url);
  try { localStorage.setItem("ll-space", state.space); } catch {}
}

async function checkAccess() {
  try { state.canWrite = (await api("/api/access")).can_write; } catch { state.canWrite = false; }
  document.body.classList.toggle("locked", !state.canWrite);
  const hours = state.statusData?.try_hours || 24;
  const name = state.family.elder_name;
  $("#spaceNote").innerHTML = state.space === "try"
    ? `Sandbox. Add your own voice notes here, no passcode needed. Anything added is visible to other visitors and clears after ${hours} hours.`
    : state.canWrite
      ? `${name ? `${esc(name)}'s archive` : "The archive"}, unlocked on this device. Changes here are permanent.`
      : `You are looking at ${name ? `${esc(name)}'s` : "their"} archive. Listen and ask freely. To add voice notes of your own, <button class="link inline" data-go-try>try it yourself</button>.`;
}

async function switchSpace(space, view) {
  if (space === state.space) { if (view) show(view); return; }
  state.space = space; state.history = []; state.memoCache = {};
  $("#thread").innerHTML = ""; $("#askIntro").classList.remove("hidden");
  audio.pause(); $("#player").classList.add("hidden"); document.body.classList.remove("player-open");
  applySpace();
  await loadFamily(); await refreshMemos(); await checkAccess();
  if (view) { history.replaceState(null, "", `${location.pathname}${location.search}#${view}`); show(view); } else route();
}

$("#spaceBar").addEventListener("click", (e) => { const b = e.target.closest("[data-space]"); if (b) switchSpace(b.dataset.space); });
document.addEventListener("click", (e) => { if (e.target.closest("[data-go-try]")) switchSpace("try", "add"); });

// Navigation

function show(view) {
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${view}`));
  $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  document.body.classList.toggle("on-ask", view === "ask");
  const loaders = { archive: loadArchive, recipes: loadRecipes, timeline: loadTimeline, planner: loadPlanner, settings: loadSettings };
  loaders[view]?.();
}

$("#tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  history.replaceState(null, "", `#${b.dataset.view}`);
  show(b.dataset.view);
});
$$("[data-back]").forEach((b) => b.addEventListener("click", () => { history.replaceState(null, "", "#archive"); show("archive"); }));

function route() {
  const h = location.hash.slice(1);
  if (h.startsWith("memo/")) { const [, id, t] = h.split("/"); openMemo(id, t ? Number(t) : null); return; }
  show(["ask", "archive", "add", "recipes", "timeline", "planner", "settings"].includes(h) ? h : "ask");
}
window.addEventListener("hashchange", route);

// Status

async function loadStatus() {
  try {
    const s = await api("/api/status");
    state.statusData = s;
    const core = [s.gemma.ok, s.atlas.ok, s.transcriber.ok];
    const okCount = core.filter(Boolean).length;
    $("#statusDot").className = "dot " + (okCount === 3 && s.atlas.search_ready ? "ok" : okCount >= 2 ? "warn" : "bad");
    $("#statusText").textContent = `Gemma ${s.gemma.ok ? "ready" : "offline"}`;
    $("#passNote").textContent = s.read_only
      ? "This archive is shared read only. Enter the passcode to add, rate or delete."
      : "No passcode is set on this server, so anyone who can open it can make changes.";
    renderStatusTable();
  } catch (e) {
    $("#statusDot").className = "dot bad"; $("#statusText").textContent = "Server unreachable";
  }
}

function renderStatusTable() {
  const s = state.statusData; if (!s) return;
  const row = (name, ok, detail) => `<tr><td><span class="dot ${ok ? "ok" : "bad"}"></span> ${esc(name)}</td><td>${esc(detail)}</td></tr>`;
  const idx = s.atlas.search_indexes || {};
  $("#statusTable").innerHTML = [
    row(`Gemma (${s.gemma.provider})`, s.gemma.ok, `${s.gemma.model}: ${s.gemma.detail}`),
    row("MongoDB Atlas", s.atlas.ok, s.atlas.detail),
    row("Atlas Search indexes", s.atlas.search_ready, Object.entries(idx).map(([k, v]) => `${k} ${v}`).join(", ") || "unknown"),
    row("Transcription", s.transcriber.ok, `${s.transcriber.backend || "none"}: ${s.transcriber.detail}`),
    row("TabPFN", s.tabpfn.ok, s.tabpfn.detail),
    row("ElevenLabs voice", s.elevenlabs.ok, s.elevenlabs.detail),
    row("Embeddings", true, s.embedding_model),
  ].join("");
}
$("#statusBtn").addEventListener("click", () => { history.replaceState(null, "", "#settings"); show("settings"); });

// Family

async function loadFamily() {
  try { state.family = await api("/api/family"); } catch { state.family = {}; }
  state.pr = PRONOUNS[state.family.pronoun] || PRONOUNS.she;
  const name = state.family.elder_name;
  $("#subtitle").textContent = name ? `The voice of ${name}` : state.space === "try" ? "Sandbox: try it with your own voice" : "Voice notes, kept and searchable";
  $("#askHeading").textContent = name ? `Ask about ${name}'s life` : "Ask about their life";
  $("#askInput").placeholder = name ? `What did ${name} say about...` : "Ask a question";
  $("#recipesHeading").textContent = name ? `${name}'s kitchen` : "Recipes";
  $("#askerList").innerHTML = (state.family.askers || []).map((a) => `<option value="${esc(a)}">`).join("");
}

function loadSettings() {
  const f = state.family;
  $("#elderName").value = f.elder_name || "";
  $("#familyName").value = f.family_name || "";
  $("#askers").value = (f.askers || []).join(", ");
  $("#pronoun").value = f.pronoun || "she";
  loadStatus();
}

$("#familyForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("/api/family", { method: "PUT", json: {
      elder_name: $("#elderName").value, family_name: $("#familyName").value, pronoun: $("#pronoun").value,
      askers: $("#askers").value.split(",").map((s) => s.trim()).filter(Boolean) } });
    await loadFamily(); $("#familySaved").textContent = "Saved"; buildSuggestions();
  } catch (err) { toast(err.message); }
});

$("#passForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  setPassKey($("#passInput").value);
  try { await api("/api/auth/check", { method: "POST" }); toast("Unlocked on this device"); $("#passInput").value = ""; checkAccess(); }
  catch { setPassKey(""); toast("That passcode did not work"); }
});

// Player

const audio = $("#audio");
const player = { memoId: null, segments: [] };

async function getMemo(id, fresh = false) {
  if (!fresh && state.memoCache[id]?.segments) return state.memoCache[id];
  const m = await api(`/api/memos/${id}`);
  state.memoCache[id] = m; return m;
}

async function playMemo(id, start = 0) {
  const m = await getMemo(id);
  if (player.memoId !== id) {
    player.memoId = id; player.segments = m.segments || [];
    audio.src = `/api/memos/${id}/audio`;
    $("#playerTitle").textContent = m.title;
  }
  $("#player").classList.remove("hidden"); document.body.classList.add("player-open");
  const go = () => { audio.currentTime = start || 0; audio.play().catch(() => {}); };
  if (audio.readyState >= 1) go(); else audio.addEventListener("loadedmetadata", go, { once: true });
}

audio.addEventListener("timeupdate", () => {
  const t = audio.currentTime;
  $("#playerTime").textContent = `${fmt(t)} / ${fmt(audio.duration)}`;
  if (audio.duration) $("#seek").value = Math.round((t / audio.duration) * 1000);
  const seg = player.segments.find((s) => t >= s.start && t <= s.end + 0.3);
  $("#playerCaption").textContent = seg ? seg.text : "";
  $$(".seg").forEach((el) => el.classList.toggle("now", seg && Number(el.dataset.start) === seg.start && el.dataset.memo === player.memoId));
});
audio.addEventListener("play", () => $("#playBtn").classList.add("playing"));
audio.addEventListener("pause", () => $("#playBtn").classList.remove("playing"));
$("#playBtn").addEventListener("click", () => (audio.paused ? audio.play() : audio.pause()));
$("#seek").addEventListener("input", (e) => { if (audio.duration) audio.currentTime = (e.target.value / 1000) * audio.duration; });
$("#closePlayer").addEventListener("click", () => { audio.pause(); $("#player").classList.add("hidden"); document.body.classList.remove("player-open"); });

// Ask

function buildSuggestions() {
  const ready = state.memos.filter((m) => m.status === "ready");
  const { obj, pos } = state.pr;
  const out = new Set();
  const recipe = ready.find((m) => m.recipe);
  if (recipe) out.add(`How do I make ${recipe.recipe.name}?`);
  const person = ready.flatMap((m) => m.people || []).find((p) => p.length < 30);
  if (person) out.add(`What did ${state.pr.sub} say about ${person}?`);
  const place = ready.flatMap((m) => m.places || []).find((p) => p.length < 30);
  if (place) out.add(`What happened in ${place}?`);
  const advice = ready.find((m) => m.kind === "advice");
  if (advice) out.add(`What advice did ${state.pr.sub} give?`);
  if (ready.length) out.add(`Tell me ${pos} life story in order`);
  if (ready.length) out.add(`When is the best time to call ${obj} this week?`);
  const empty = state.memos.length === 0;
  document.body.classList.toggle("empty-archive", empty);
  $("#suggestions").innerHTML = ready.length
    ? [...out].slice(0, 5).map((s) => `<button class="ghost">${esc(s)}</button>`).join("")
    : `<div class="empty-start"><p>${state.space === "try" ? "The sandbox is empty right now. Record yourself or upload any voice note, then come back and ask it questions." : "The archive is empty. Add a voice note and Legacy Loom will listen, file it and have answers ready."}</p><a class="primary like-btn" href="#add">Add the first voice note</a><a class="app-help" href="/guide">New here? Read the five minute guide</a></div>`;
}
$("#suggestions").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) ask(b.textContent); });

$("#askForm").addEventListener("submit", (e) => { e.preventDefault(); const q = $("#askInput").value.trim(); if (q) ask(q); });

function renderAnswer(text, sources) {
  let html = esc(text).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\[(\d+)\]/g, (m, n) => (sources[n - 1] ? `<button class="cite" data-n="${n}" title="${esc(sources[n - 1].title)}">${n}</button>` : m));
  return html;
}

async function ask(question) {
  $("#askInput").value = ""; $("#askIntro").classList.add("hidden");
  const thread = $("#thread");
  thread.insertAdjacentHTML("beforeend", `<div class="q">${esc(question)}</div>`);
  const a = document.createElement("div"); a.className = "a";
  a.innerHTML = `<div class="steps"><span>Thinking about where to look</span></div><div class="body thinking"></div><div class="sources hidden"></div><div class="actions hidden"></div>`;
  thread.appendChild(a); a.scrollIntoView({ behavior: "smooth", block: "end" });
  $("#askBtn").disabled = true;

  let text = "", sources = [];
  const steps = $(".steps", a), body = $(".body", a);
  try {
    const res = await fetch("/api/ask", { method: "POST", headers: { "content-type": "application/json", "x-space": state.space }, body: JSON.stringify({ question, history: state.history }) });
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, i); buf = buf.slice(i + 2);
        if (!line.startsWith("data:")) continue;
        const ev = JSON.parse(line.slice(5));
        if (ev.type === "plan") steps.innerHTML = "";
        if (ev.type === "tool") {
          const label = { search_memories: "Searched memories", find_recipe: "Looked up recipes", timeline: "Read the timeline", plan_calls: "Asked TabPFN" }[ev.tool] || ev.tool;
          steps.insertAdjacentHTML("beforeend", `<span>${esc(label)}${ev.query && ev.tool !== "timeline" && ev.tool !== "plan_calls" ? `: "${esc(ev.query)}"` : ""}${ev.modes ? ` (${ev.modes.join(" + ")})` : ""}</span>`);
        }
        if (ev.type === "sources") sources = ev.sources;
        if (ev.type === "token") { text += ev.text; body.innerHTML = renderAnswer(text, sources); }
        if (ev.type === "removed_quotes") {
          steps.insertAdjacentHTML("beforeend", `<span title="${esc(ev.quotes.join(" | "))}">Removed ${ev.quotes.length} quote${ev.quotes.length > 1 ? "s" : ""} that ${ev.quotes.length > 1 ? "were" : "was"} never said</span>`);
        }
        if (ev.type === "error") throw new Error(ev.message);
        if (ev.type === "done") finishAnswer(a, question, ev, sources);
      }
    }
  } catch (err) {
    body.classList.remove("thinking");
    body.innerHTML = `<span class="error">Could not answer: ${esc(err.message)}</span>`;
  } finally {
    $("#askBtn").disabled = false;
  }
}

function finishAnswer(a, question, ev, sources) {
  const body = $(".body", a); body.classList.remove("thinking");
  body.innerHTML = renderAnswer(ev.text, sources);
  state.history.push({ role: "user", content: question }, { role: "assistant", content: ev.text });
  state.history = state.history.slice(-8);
  const cited = new Set(ev.cited);
  const list = $(".sources", a);
  const shown = sources.filter((s, i) => cited.has(i + 1) || cited.size === 0).slice(0, 6);
  if (shown.length) {
    list.classList.remove("hidden");
    list.innerHTML = sources.map((s, i) => (cited.has(i + 1) || cited.size === 0) ? `
      <div class="source ${cited.has(i + 1) ? "cited" : ""}">
        <span class="num">${i + 1}</span>
        <div style="flex:1;min-width:0"><strong>${esc(s.title)}</strong> <span class="muted small">${s.overview ? "summary" : fmt(s.start)}</span><p>${esc(s.text)}</p></div>
        <button class="ghost" data-play="${s.memo_id}" data-start="${s.overview ? 0 : s.start}">Play</button>
      </div>` : "").join("");
  }
  a.dataset.sources = JSON.stringify(sources);
  const acts = $(".actions", a); acts.classList.remove("hidden");
  acts.innerHTML = `<button class="ghost" data-speak>Read it aloud</button>`;
  if (ev.ask_her) {
    acts.insertAdjacentHTML("beforebegin", `<div class="ask-her">Next time, ask: <em>${esc(ev.ask_her)}</em></div>`);
    acts.insertAdjacentHTML("beforeend", `<button class="ghost" data-save-q="${esc(ev.ask_her)}">Save question for the next call</button>`);
  }
}

$("#thread").addEventListener("click", async (e) => {
  const a = e.target.closest(".a");
  const cite = e.target.closest(".cite");
  if (cite && a) {
    const s = JSON.parse(a.dataset.sources || "[]")[cite.dataset.n - 1];
    if (s) playMemo(s.memo_id, s.overview ? 0 : s.start);
  }
  const p = e.target.closest("[data-play]");
  if (p) playMemo(p.dataset.play, Number(p.dataset.start));
  const sp = e.target.closest("[data-speak]");
  if (sp) {
    sp.disabled = true; sp.textContent = "Getting the voice ready";
    try {
      const copy = $(".body", a).cloneNode(true);
      $$(".cite", copy).forEach((c) => c.remove());
      const text = copy.textContent.replace(/\s+([.,;:!?])/g, "$1");
      const res = await fetch("/api/tts", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ text }) });
      if (!res.ok) throw new Error((await res.json()).detail);
      const url = URL.createObjectURL(await res.blob());
      audio.pause(); new Audio(url).play();
      sp.textContent = "Read it aloud";
    } catch (err) { toast(err.message); sp.textContent = "Read it aloud"; }
    sp.disabled = false;
  }
  const sq = e.target.closest("[data-save-q]");
  if (sq) {
    try { await api("/api/questions", { method: "POST", json: { text: sq.dataset.saveQ } }); sq.textContent = "Saved"; sq.disabled = true; }
    catch (err) { toast(err.message); }
  }
});

// Archive

async function refreshMemos() {
  try { state.memos = await api("/api/memos"); } catch (e) { state.memos = []; toast(e.message); }
  buildSuggestions();
  return state.memos;
}

function stars(m) {
  return `<div class="stars-wrap"><div class="stars" data-rate="${m._id}">${[1, 2, 3, 4, 5].map((n) => `<button data-n="${n}" class="${(m.rating || 0) >= n ? "on" : ""}" aria-label="${n} stars">&#9733;</button>`).join("")}</div><div class="stars-label">${m.rating ? (m.rating >= 4 ? "A keeper" : "Rated") : "How good was this?"}</div></div>`;
}

function memoCard(m) {
  const d = m.recorded_at ? new Date(m.recorded_at) : null;
  const when = d ? d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "";
  const busy = m.status !== "ready";
  const date = d ? `<div class="date" aria-hidden="true"><b>${d.getDate()}</b><span>${d.toLocaleDateString([], { month: "short" })}</span><span>${d.toLocaleDateString([], { weekday: "short" })}</span></div>` : `<div class="date"></div>`;
  return `<article class="memo" data-id="${m._id}">
    ${date}
    <div class="open" data-open="${m._id}">
      <h3>${esc(m.title)}</h3>
      <div class="meta"><span class="badge ${esc(m.kind || "")}">${esc(m.kind || "new")}</span><span>${esc(when)}</span>${m.duration ? `<span>${fmt(m.duration)}</span>` : ""}${m.asked_by ? `<span>asked by ${esc(m.asked_by)}</span>` : ""}${m.year ? `<span>${m.year}</span>` : m.era ? `<span>${esc(m.era)}</span>` : ""}</div>
      ${busy ? `<div class="progress">${m.status === "error" ? `<span class="error">Failed: ${esc(m.error || "")}</span>` : `<span class="spinner"></span>${esc(STAGES[m.status] || m.status)}`}</div>` : `<p class="summary">${esc(m.summary)}</p>`}
    </div>
    <div class="memo-side">
      <button class="play" data-play="${m._id}" data-start="0" aria-label="Play ${esc(m.title)}"></button>
      ${busy ? "" : stars(m)}
    </div>
  </article>`;
}

async function loadArchive() {
  const memos = await refreshMemos();
  $("#archiveCount").textContent = memos.length ? `${memos.length} recording${memos.length === 1 ? "" : "s"}, ${fmt(memos.reduce((s, m) => s + (m.duration || 0), 0))} of ${state.family.elder_name ? `${state.family.elder_name}'s` : "their"} voice` : "";
  $("#memoList").innerHTML = memos.length ? memos.map(memoCard).join("") : `<div class="empty">Nothing here yet. <a href="#add">Add the first recording.</a></div>`;
  if (memos.some((m) => !["ready", "error"].includes(m.status))) {
    clearTimeout(loadArchive.t); loadArchive.t = setTimeout(() => { if ($("#view-archive").classList.contains("active")) loadArchive(); }, 4000);
  }
}

document.addEventListener("click", async (e) => {
  const open = e.target.closest("[data-open]");
  if (open) { location.hash = `memo/${open.dataset.open}`; return; }
  const play = e.target.closest(".memo [data-play], #memoDetail [data-play], .timeline [data-play], .recipe-card [data-play]");
  if (play) { playMemo(play.dataset.play, Number(play.dataset.start || 0)); return; }
  const star = e.target.closest("[data-rate] button");
  if (star) {
    const id = star.parentElement.dataset.rate, n = Number(star.dataset.n);
    try {
      await api(`/api/memos/${id}`, { method: "PATCH", json: { rating: n } });
      $$("button", star.parentElement).forEach((b) => b.classList.toggle("on", Number(b.dataset.n) <= n));
      star.parentElement.nextElementSibling.textContent = n >= 4 ? "A keeper" : "Rated";
    } catch (err) { toast(err.message); }
  }
});

// Memo detail

async function openMemo(id, t) {
  show("memo");
  $("#memoDetail").innerHTML = `<div class="empty"><span class="spinner"></span></div>`;
  let m;
  try { m = await getMemo(id, true); } catch (e) { $("#memoDetail").innerHTML = `<p class="error">${esc(e.message)}</p>`; return; }
  const r = m.recipe;
  const when = new Date(m.recorded_at).toLocaleString([], { dateStyle: "full", timeStyle: "short" });
  $("#memoDetail").innerHTML = `
    <div class="detail-head"><button class="play" data-play="${m._id}" data-start="0" aria-label="Play"></button>
      <div><h2>${esc(m.title)}</h2><p class="muted">Recorded ${esc(when)}${m.asked_by ? `, asked by ${esc(m.asked_by)}` : ""}${m.duration ? `, ${fmt(m.duration)}` : ""}</p></div></div>
    ${m.status !== "ready" ? `<div class="card progress">${m.status === "error" ? `<span class="error">${esc(m.error)}</span>` : `<span class="spinner"></span>${esc(STAGES[m.status])}`}</div>` : ""}
    ${m.summary ? `<p style="font-size:17px">${esc(m.summary)}</p>` : ""}
    <div class="facts">${m.year ? `<span>${m.year}</span>` : ""}${m.era ? `<span>${esc(m.era)}</span>` : ""}${(m.people || []).map((p) => `<span>${esc(p)}</span>`).join("")}${(m.places || []).map((p) => `<span>${esc(p)}</span>`).join("")}${m.language ? `<span>spoken in ${esc(m.language)}</span>` : ""}${m.speech?.wpm ? `<span>${m.speech.wpm} words a minute</span>` : ""}</div>
    ${(m.quotes || []).map((q) => `<p class="quote">&ldquo;${esc(q)}&rdquo;</p>`).join("")}
    ${r ? `<div class="card recipe-box"><h3>${esc(r.name)}</h3>${r.serves ? `<p class="muted">Serves ${esc(r.serves)}</p>` : ""}<strong>Ingredients</strong><ul>${r.ingredients.map((i) => `<li>${esc(i)}</li>`).join("")}</ul><strong>Method</strong><ol>${r.steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>${r.tips?.length ? `<strong>Tips</strong><ul>${r.tips.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>` : ""}</div>` : ""}
    ${m.status === "ready" ? `<div class="card"><div class="row" style="justify-content:space-between"><h3>Rate this recording</h3>${stars(m)}</div><p class="muted small">Ratings teach the call planner which sessions go well.</p></div>` : ""}
    <div class="card"><h3>Transcript</h3><p class="muted small">Tap a line to hear it.</p><div class="transcript">${(m.segments || []).map((s) => `<div class="seg" data-memo="${m._id}" data-start="${s.start}"><time>${fmt(s.start)}</time><span>${esc(s.text)}</span></div>`).join("") || `<p class="muted">Not transcribed yet.</p>`}</div></div>
    <div class="card"><h3>Fix the details</h3><div class="row"><input id="editTitle" value="${esc(m.title)}"><input id="editYear" type="number" placeholder="Year" value="${m.year || ""}" style="max-width:110px"><button class="ghost" id="saveEdit">Save</button></div>
      <div class="row" style="margin-top:12px"><button class="ghost" id="reprocess">Run it through again</button><button class="link danger" id="deleteMemo">Delete this recording</button></div></div>`;
  $$(".seg").forEach((el) => el.addEventListener("click", () => playMemo(id, Number(el.dataset.start))));
  $("#saveEdit").onclick = async () => {
    try { await api(`/api/memos/${id}`, { method: "PATCH", json: { title: $("#editTitle").value, year: $("#editYear").value ? Number($("#editYear").value) : null } }); toast("Saved"); }
    catch (err) { toast(err.message); }
  };
  $("#reprocess").onclick = async () => { try { await api(`/api/memos/${id}/reprocess`, { method: "POST" }); toast("Queued again"); openMemo(id); } catch (err) { toast(err.message); } };
  $("#deleteMemo").onclick = async () => {
    if (!confirm("Delete this recording and everything made from it? This cannot be undone.")) return;
    try { await api(`/api/memos/${id}`, { method: "DELETE" }); location.hash = "archive"; } catch (err) { toast(err.message); }
  };
  if (m.status !== "ready" && m.status !== "error") setTimeout(() => { if (location.hash === `#memo/${id}`) openMemo(id); }, 4000);
  if (t !== null && t !== undefined) playMemo(id, t);
}

// Add

let pendingFiles = [];
const pad = (n) => String(n).padStart(2, "0");
const localIso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;

function stageFiles(files) {
  pendingFiles = files;
  $("#uploadForm").classList.remove("hidden");
  $("#pendingFiles").innerHTML = files.map((f) => `<span>${esc(f.name)}</span>`).join("");
  $("#recordedAt").value = localIso(new Date(files[0].lastModified || Date.now()));
  if (!$("#askedBy").value && state.family.askers?.length) $("#askedBy").value = state.family.askers[0];
  $("#uploadForm").scrollIntoView({ behavior: "smooth" });
}

$("#fileInput").addEventListener("change", (e) => { if (e.target.files.length) stageFiles([...e.target.files]); e.target.value = ""; });
const drop = $("#drop");
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); const f = [...e.dataTransfer.files].filter((x) => x.type.startsWith("audio") || /\.(opus|ogg|m4a|amr|mp3|wav|webm)$/i.test(x.name)); if (f.length) stageFiles(f); });
$("#cancelUpload").addEventListener("click", () => { pendingFiles = []; $("#uploadForm").classList.add("hidden"); });

$("#uploadForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const log = $("#uploadLog");
  const files = pendingFiles; pendingFiles = []; $("#uploadForm").classList.add("hidden");
  // Several files share one form: keep their own timestamps when they carry one.
  for (const f of files) {
    const when = files.length > 1 && f.lastModified ? localIso(new Date(f.lastModified)) : $("#recordedAt").value;
    const fd = new FormData();
    fd.append("file", f, f.name); fd.append("recorded_at", when);
    fd.append("asked_by", $("#askedBy").value); fd.append("prompt_topic", $("#promptTopic").value);
    const line = document.createElement("p"); log.prepend(line);
    const maxMb = state.statusData?.max_upload_mb || 60;
    if (f.size > maxMb * 1024 * 1024) { line.innerHTML = `<span class="error">${esc(f.name)} is over ${maxMb} MB. Trim it or send a shorter note.</span>`; continue; }
    line.innerHTML = state.statusData?.serverless
      ? `<span class="spinner"></span> Listening to ${esc(f.name)} and filing it. This takes about a minute.`
      : `<span class="spinner"></span> Uploading ${esc(f.name)}`;
    try {
      const r = await api("/api/memos", { method: "POST", body: fd });
      line.innerHTML = r.status === "error"
        ? `<span class="error">${esc(f.name)}: ${esc(r.error)}</span>`
        : `${esc(f.name)} ${r.status === "ready" ? "is in the archive" : "saved"}. <a href="#memo/${r.id}">Open it</a>`;
    } catch (err) { line.innerHTML = `<span class="error">${esc(f.name)}: ${esc(err.message)}</span>`; }
  }
  $("#promptTopic").value = "";
});

let recorder = null, recStart = 0, recTimer = null;
$("#recBtn").addEventListener("click", async () => {
  if (recorder) { recorder.stop(); return; }
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }); }
  catch { toast("Microphone permission was not given"); return; }
  const chunks = [];
  const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "";
  recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : {});
  recorder.ondataavailable = (ev) => ev.data.size && chunks.push(ev.data);
  recorder.onstop = () => {
    stream.getTracks().forEach((t) => t.stop()); clearInterval(recTimer);
    $("#recBtn").classList.remove("on"); $("#recBtn").lastChild.textContent = "Start recording";
    const started = new Date(recStart);
    const ext = (recorder.mimeType || "").includes("mp4") ? "m4a" : "webm";
    const file = new File(chunks, `recording-${localIso(started).replace(":", "")}.${ext}`, { type: recorder.mimeType || "audio/webm", lastModified: recStart });
    recorder = null; $("#recTime").textContent = `Recorded ${fmt((Date.now() - recStart) / 1000)}`;
    stageFiles([file]);
  };
  recorder.start(1000); recStart = Date.now();
  $("#recBtn").classList.add("on"); $("#recBtn").lastChild.textContent = "Stop recording";
  recTimer = setInterval(() => ($("#recTime").textContent = fmt((Date.now() - recStart) / 1000)), 500);
});

// Recipes

async function loadRecipes() {
  let items = [];
  try { items = await api("/api/recipes"); } catch (e) { toast(e.message); }
  $("#recipeGrid").innerHTML = items.length ? items.map((r) => `
    <div class="recipe-card"><h3>${esc(r.recipe.name)}</h3>
      ${r.quotes[0] ? `<p class="quote" style="font-size:16px">&ldquo;${esc(r.quotes[0])}&rdquo;</p>` : ""}
      <ul>${r.recipe.ingredients.slice(0, 6).map((i) => `<li>${esc(i)}</li>`).join("")}${r.recipe.ingredients.length > 6 ? `<li class="muted">and ${r.recipe.ingredients.length - 6} more</li>` : ""}</ul>
      <div class="row"><button class="ghost" data-play="${r.memo_id}" data-start="0">Hear ${esc(state.pr.obj)} tell it</button><a class="link" href="#memo/${r.memo_id}">Full recipe</a></div>
    </div>`).join("") : `<div class="empty">No recipes yet. When ${esc(state.family.elder_name || "they")} explains a dish in a recording, it lands here and in the printable book.</div>`;
}

// Timeline

async function loadTimeline() {
  let items = [];
  try { items = await api("/api/timeline"); } catch (e) { toast(e.message); }
  $("#timelineList").innerHTML = items.length ? items.map((m) => `
    <li><span class="yr">${m.year || esc(m.era || "?")}</span><div><h3 data-open="${m._id}">${esc(m.title)}</h3><p>${esc(m.summary)}</p><button class="link" data-play="${m._id}" data-start="0">Listen</button></div></li>`).join("")
    : `<div class="empty">The timeline fills in as memories are added.</div>`;
}

// Planner

async function loadPlanner() {
  const body = $("#plannerBody");
  body.innerHTML = `<div class="card"><span class="spinner"></span> Asking TabPFN</div>`;
  loadQuestions(); loadSessions();
  let p;
  try { p = await api("/api/planner"); } catch (e) { body.innerHTML = `<div class="card error">${esc(e.message)}</div>`; return; }
  if (!p.ready) {
    const pct = Math.min(100, Math.round((p.rated / Math.max(1, p.rated + p.need_more)) * 100));
    body.innerHTML = `<div class="card"><h3>Learning your rhythm</h3><p>${esc(p.reason)}</p><div class="meter"><div style="width:${pct}%"></div></div><p class="muted small">${p.rated} rated of ${p.sessions} sessions, ${p.keepers} keepers.</p></div>`;
    return;
  }
  const cell = (v) => `background:color-mix(in srgb, var(--accent) ${Math.round(v * 100)}%, var(--paper-2));color:${v > .55 ? "#fff" : "var(--ink-2)"}`;
  const hours = []; for (let h = p.hours[0]; h <= p.hours[1]; h++) hours.push(h);
  const days = [...new Set(p.heatmap.map((x) => x.day))];
  const look = Object.fromEntries(p.heatmap.map((x) => [`${x.day}-${x.hour}`, x.p]));
  body.innerHTML = `
    <div class="card">
      <h3>Best times to call ${esc(state.family.elder_name || "")} this week</h3>
      <p class="muted small">${esc(p.engine)}, learned from ${p.rated} rated sessions (${p.keepers} keepers). Calls made by ${esc(p.asked_by)}.</p>
      <div class="best">${p.best.map((b) => `<div class="slot"><strong>${esc(b.label)}</strong><span class="pct">${Math.round(b.p_keeper * 100)}%</span><div class="muted small">chance of a keeper</div><div class="small">Ask about <b>${esc(b.topic)}</b>${b.minutes ? `, ${esc(state.pr.sub)} may talk for about ${Math.max(1, Math.round(b.minutes))} min` : ""}</div></div>`).join("")}</div>
    </div>
    <div class="split" style="margin-top:0">
      <div class="card"><h3>By day and hour</h3>
        <div class="heat" style="grid-template-columns:44px repeat(${hours.length}, minmax(24px,1fr))">
          <div></div>${hours.map((h) => `<div class="lbl" style="justify-content:center">${h}</div>`).join("")}
          ${days.map((d) => `<div class="lbl">${d}</div>${hours.map((h) => `<div style="${cell(look[`${d}-${h}`] || 0)}" title="${d} ${h}:00, ${Math.round((look[`${d}-${h}`] || 0) * 100)}%"></div>`).join("")}`).join("")}
        </div></div>
      <div class="card"><h3>Topics that go well</h3><div class="bars">${p.topics.map((t) => `<div class="bar"><span>${esc(t.topic)}</span><div class="track"><div class="fill" style="width:${Math.round(t.p * 100)}%"></div></div><span>${Math.round(t.p * 100)}%</span></div>`).join("")}</div></div>
    </div>`;
}

async function loadQuestions() {
  let qs = [];
  try { qs = await api("/api/questions"); } catch {}
  $("#qList").innerHTML = qs.map((q) => `<li class="${q.done ? "done" : ""}"><input type="checkbox" data-q="${q._id}" ${q.done ? "checked" : ""}><span>${esc(q.text)}</span><button class="link" data-qdel="${q._id}">Remove</button></li>`).join("") || `<li class="muted small">When an answer comes up empty, Legacy Loom suggests a question. Save it here.</li>`;
}
$("#qForm").addEventListener("submit", async (e) => {
  e.preventDefault(); const text = $("#qInput").value.trim(); if (!text) return;
  try { await api("/api/questions", { method: "POST", json: { text } }); $("#qInput").value = ""; loadQuestions(); } catch (err) { toast(err.message); }
});
$("#qList").addEventListener("click", async (e) => {
  const cb = e.target.closest("[data-q]"), del = e.target.closest("[data-qdel]");
  try {
    if (cb) { await api(`/api/questions/${cb.dataset.q}`, { method: "PATCH", json: { done: cb.checked } }); loadQuestions(); }
    if (del) { await api(`/api/questions/${del.dataset.qdel}`, { method: "DELETE" }); loadQuestions(); }
  } catch (err) { toast(err.message); }
});

async function loadSessions() {
  let rows = [];
  try { rows = await api("/api/sessions"); } catch {}
  const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  $("#sessionTable").innerHTML = rows.length ? `<div class="table-wrap"><table><tr><th>When</th><th>Who</th><th>Topic</th><th>Min</th><th>Rating</th></tr>${rows.slice().reverse().map((r) => `<tr><td>${days[r.weekday]} ${esc((r.when || "").slice(0, 10))} ${pad(r.hour)}:00</td><td>${esc(r.asked_by)}</td><td>${esc(r.topic)}</td><td>${r.duration_min ?? ""}</td><td>${r.rating ? "&#9733;".repeat(r.rating) : `<span class="muted">unrated</span>`}</td></tr>`).join("")}</table></div>` : `<p class="muted small">No sessions yet.</p>`;
}
$("#csvInput").addEventListener("change", async (e) => {
  const f = e.target.files[0]; if (!f) return;
  const fd = new FormData(); fd.append("file", f);
  try { const r = await api("/api/sessions/import", { method: "POST", body: fd }); toast(`Imported ${r.added} sessions${r.problems.length ? `, ${r.problems.length} lines skipped` : ""}`); loadPlanner(); }
  catch (err) { toast(err.message); }
  e.target.value = "";
});

// Boot

(async function boot() {
  state.space = initialSpace();
  applySpace();
  await loadStatus();
  await loadFamily();
  await refreshMemos();
  await checkAccess();
  route();
})();
