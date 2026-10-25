/* Thought → Speak v2 — front-end (no build step, no external deps) */
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtTime = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const DIMS = ["structure", "clarity", "relevance", "answer_quality", "delivery"];

const state = {
  config: null, modes: {}, mode: "Interview", prompt: "", customPrompt: false,
  ctx: null, target: [60, 120], journey: null, lesson: null,
  chainId: null, takeNo: 1, lastAttempt: null, rec: null,
  recording: false, audioChunks: [], audioRate: 48000, cap: null, peakLevel: 0, noSR: false,
  elapsed: 0, tickStart: null, timerId: null, spokeSeconds: 0,
};

const TZ = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (_) { return ""; } })();

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json", "X-Timezone": TZ }, credentials: "same-origin", ...opts });
  const data = await r.json().catch(() => ({}));
  if (r.status === 401 && !path.startsWith("/api/auth/")) showAuth("signin", "Your session ended — please sign in again.");
  if (!r.ok) throw new Error(data.detail || `Request failed (${r.status})`);
  return data;
}

/* ------------------------------------------------------------------ theme */
(function initTheme() {
  try { const t = localStorage.getItem("tts-theme"); if (t) document.documentElement.dataset.theme = t; } catch (_) {}
  $("themeBtn").onclick = () => {
    const cur = document.documentElement.dataset.theme ||
      (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("tts-theme", next); } catch (_) {}
  };
})();

/* ------------------------------------------------------------------ tabs */
document.querySelectorAll(".tab").forEach((b) => b.onclick = () => showTab(b.dataset.tab));
function showTab(name) {
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tabpanel").forEach((p) => p.classList.toggle("hidden", p.id !== `tab-${name}`));
  if (name === "learn") loadLearn();
  if (name === "pronounce") loadPronounce();
  if (name === "progress") loadProgress();
  if (name === "history") loadHistory();
  window.scrollTo({ top: 0 });
}

/* ------------------------------------------------------------------ setup */
async function init() {
  state.config = await api("/api/config");
  state.config.modes.forEach((m) => (state.modes[m.name] = m));
  const badge = $("providerBadge");
  if (state.config.provider === "local") {
    badge.textContent = "Local analysis (no API key)";
    badge.classList.add("local");
    badge.title = "Set OPENAI_API_KEY (or ANTHROPIC_API_KEY) in .env for AI coaching";
  } else {
    badge.textContent = `AI: ${state.config.provider} · ${state.config.model}`;
  }
  const stt = { openai: "Whisper API", local: "local Whisper", none: "browser only" }[state.config.transcriber] || state.config.transcriber;
  badge.textContent += ` · Transcript: ${stt}`;
  $("modeChips").innerHTML = state.config.modes.map((m) =>
    `<button class="chip" role="radio" data-mode="${esc(m.name)}">${esc(m.name)}</button>`).join("");
  $("modeChips").querySelectorAll(".chip").forEach((c) => (c.onclick = () => { clearCtx(); setMode(c.dataset.mode); }));
  $("historyMode").innerHTML += state.config.modes.map((m) => `<option>${esc(m.name)}</option>`).join("");
  let saved = null;
  try { saved = localStorage.getItem("tts-mode"); } catch (_) {}
  setMode(state.modes[saved] ? saved : "Interview");
  setupSpeech();
  showTab("learn");
}

const READ_ALOUD = "Read aloud";
function setMode(mode, prompt) {
  const ra = mode === READ_ALOUD;
  $("modeChips").classList.toggle("hidden", ra);
  $("customPromptBtn").classList.toggle("hidden", ra);
  $("promptLabel").textContent = ra ? "Read this aloud" : "Your prompt";
  if (ra) return setReadAloud(prompt);
  state.mode = mode; state.lastMode = mode;
  try { localStorage.setItem("tts-mode", mode); } catch (_) {}
  document.querySelectorAll("#modeChips .chip").forEach((c) => {
    const on = c.dataset.mode === mode; c.classList.toggle("active", on); c.setAttribute("aria-checked", on);
  });
  const m = state.modes[mode];
  const labels = state.ctx?.framework || m.framework;
  const target = state.ctx?.target || m.target_seconds;
  state.target = target;
  $("frameworkPills").innerHTML = (state.ctx?.frameworkName ? `<b class="small">${esc(state.ctx.frameworkName)}:</b> ` : "") +
    labels.map((f) => `<span class="pill">${esc(f)}</span>`).join("");
  $("targetTime").textContent = `· target ${target[0]}–${target[1]}s`;
  const [lo, hi] = target, max = hi * 1.5;
  $("timeTarget").style.left = `${(lo / max) * 100}%`;
  $("timeTarget").style.width = `${((hi - lo) / max) * 100}%`;
  setPrompt(prompt || m.prompts[Math.floor(Math.random() * m.prompts.length)]);
}

/* Practice context: a lesson exercise, today's warm-up, or a review drill. */
function practiceWith({ mode, prompt, ctx = null, challenge = false }) {
  state.ctx = ctx;
  showTab("practice");
  setMode(mode, prompt);
  $("challenge").checked = !!challenge;
  renderCtxBanner();
}
function clearCtx() { state.ctx = null; $("recBanner").classList.add("hidden"); }

function setReadAloud(text) {
  state.mode = READ_ALOUD;
  document.querySelectorAll("#modeChips .chip").forEach((c) => c.classList.remove("active"));
  const target = state.ctx?.target || [4, 15];
  state.target = target;
  $("frameworkPills").innerHTML = `<button class="ghost sm" id="hearTarget">▶ Hear it</button>
    <span class="small muted">Tap any word for its stress · then read at a natural pace</span>`;
  $("targetTime").textContent = "";
  const max = target[1] * 1.5;
  $("timeTarget").style.left = `${(target[0] / max) * 100}%`;
  $("timeTarget").style.width = `${((target[1] - target[0]) / max) * 100}%`;
  setPrompt(text);
  $("promptText").innerHTML = wordsHtml(text, state.ctx?.focus || []);
  $("hearTarget").onclick = () => speak(state.prompt, 0.85);
}
function renderCtxBanner() {
  const c = state.ctx, b = $("recBanner");
  if (!c) { b.classList.add("hidden"); return; }
  const back = c.kind === "lesson" ? `<button class="ghost sm" id="ctxBack">← Lesson</button>` : `<button class="ghost sm" id="ctxBack">← Today</button>`;
  b.innerHTML = `<div><b>${esc(c.title)}</b>${c.constraint ? ` — ${esc(c.constraint)}` : ""}
      ${c.steps?.length ? `<div class="small muted">${c.steps.map(esc).join(" · ")}</div>` : ""}</div>
    <div class="row gap-s">${back}<button class="ghost sm" id="closeBanner" aria-label="Leave this exercise">✕</button></div>`;
  b.classList.remove("hidden");
  $("closeBanner").onclick = () => { clearCtx(); setMode(state.lastMode || "Interview"); };
  $("ctxBack").onclick = () => {
    if (c.from === "pronounce") return showTab("pronounce");
    showTab("learn"); if (c.kind === "lesson") openLesson(c.lessonId);
  };
  if (c.from === "pronounce") $("ctxBack").textContent = "← Drills";
}

function setPrompt(p) {
  state.prompt = p; state.customPrompt = false;
  $("promptText").textContent = p; $("promptText").classList.remove("hidden");
  $("promptEdit").classList.add("hidden");
  $("customPromptBtn").textContent = "✎ Custom";
  newChain();
}

function newChain() {
  state.chainId = null; state.takeNo = 1; state.lastAttempt = null;
  $("takeLabel").textContent = "Take 1";
  $("previousTakeCard").classList.add("hidden");
  $("compareCard").classList.add("hidden");
  resetTake();
}

function resetTake() {
  stopRecording();
  $("transcript").value = ""; $("interim").classList.add("hidden");
  state.audioChunks = []; $("audioLen").textContent = "";
  state.elapsed = 0; state.spokeSeconds = 0; renderTimer();
  state.idemKey = null;                       // a new take gets a new idempotency key
  $("status").textContent = "";
}

$("newPromptBtn").onclick = async () => {
  if (state.ctx?.prompts?.length > 1) {  // lessons cycle through their own prompts
    const ps = state.ctx.prompts, i = ps.indexOf(state.prompt);
    const next = ps[(i + 1) % ps.length];
    if (state.mode === READ_ALOUD) setReadAloud(next); else setPrompt(next);
    return;
  }
  if (state.mode === READ_ALOUD) { clearCtx(); return setMode(state.lastMode || "Interview"); }
  clearCtx(); setMode(state.mode);
  const r = await api(`/api/prompt?mode=${encodeURIComponent(state.mode)}&exclude=${encodeURIComponent(state.prompt)}`);
  setPrompt(r.prompt);
};
$("customPromptBtn").onclick = () => {
  if (!state.customPrompt) {
    state.customPrompt = true;
    $("promptEdit").value = ""; $("promptEdit").classList.remove("hidden"); $("promptText").classList.add("hidden");
    $("customPromptBtn").textContent = "✓ Use this"; $("promptEdit").focus();
  } else {
    const v = $("promptEdit").value.trim();
    if (v) setPrompt(v); else { state.customPrompt = false; $("promptText").classList.remove("hidden"); $("promptEdit").classList.add("hidden"); $("customPromptBtn").textContent = "✎ Custom"; }
  }
};
$("clearBtn").onclick = resetTake;
$("retryBtn").onclick = startRetry;
$("freshBtn").onclick = () => $("newPromptBtn").click();

function startRetry() {
  if (!state.lastAttempt) return;
  state.takeNo = state.lastAttempt.attempt_no + 1;
  $("takeLabel").textContent = `Take ${state.takeNo}`;
  $("previousTakeText").textContent = state.lastAttempt.transcript;
  $("previousTakeCard").classList.remove("hidden");
  resetTake();
  $("status").textContent = "New take — same prompt. Focus on the next challenge.";
  $("transcript").scrollIntoView({ behavior: "smooth", block: "center" });
}

/* ------------------------------------------------------------------ speech + timer */
function setupSpeech() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!navigator.mediaDevices?.getUserMedia) {
    $("recordBtn").disabled = true;
    $("status").textContent = "Microphone recording isn't available in this browser. You can type or paste.";
  }
  if (!SR) {
    state.noSR = true;
    $("status").textContent = state.config.transcriber === "none"
      ? "No live transcript in this browser and no Whisper configured — use Chrome/Edge, or type your answer."
      : "Live transcript unavailable here — your recording will be transcribed by Whisper after you stop.";
    return;
  }
  const rec = new SR();
  rec.continuous = true; rec.interimResults = true; rec.lang = "en-US";
  rec.onresult = (e) => {
    let finalText = "", interim = "";
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const t = e.results[i][0].transcript;
      if (e.results[i].isFinal) finalText += t + " "; else interim += t;
    }
    if (finalText.trim()) {
      const ta = $("transcript");
      ta.value = (ta.value.trim() ? ta.value.trim() + " " : "") + finalText.trim();
    }
    $("interim").textContent = interim ? `…${interim}` : "";
    $("interim").classList.toggle("hidden", !interim);
  };
  rec.onerror = (e) => {
    if (e.error === "no-speech" || e.error === "aborted") return;
    $("status").textContent = e.error === "not-allowed"
      ? "Microphone permission denied. Allow mic access in your browser." : `Speech error: ${e.error}`;
    stopRecording();
  };
  rec.onend = () => { if (state.recording) { try { rec.start(); } catch (_) {} } };
  state.rec = rec;
}

$("recordBtn").onclick = () => (state.recording ? stopRecording() : startRecording());

async function startRecording() {
  try {
    await startCapture();
  } catch (e) {
    $("status").textContent = e.name === "NotAllowedError" ? "Microphone permission denied. Allow mic access in your browser."
      : `Could not open the microphone: ${e.message}`;
    return;
  }
  state.recording = true;
  if (state.rec) { try { state.rec.start(); } catch (_) {} }
  $("recordBtn").textContent = "■ Stop"; $("recordBtn").classList.add("on");
  $("status").textContent = "Listening… speak naturally.";
  state.tickStart = performance.now();
  state.timerId = setInterval(renderTimer, 250);
}

function stopRecording() {
  if (!state.recording) return;
  state.recording = false;
  if (state.rec) { try { state.rec.stop(); } catch (_) {} }
  stopCapture();
  state.elapsed += (performance.now() - state.tickStart) / 1000;
  state.spokeSeconds = state.elapsed;
  clearInterval(state.timerId);
  $("recordBtn").textContent = "● Start speaking"; $("recordBtn").classList.remove("on");
  $("interim").classList.add("hidden");
  $("status").textContent = "Stopped. Hit Analyze.";
  renderTimer();
}

function currentSeconds() {
  return state.elapsed + (state.recording ? (performance.now() - state.tickStart) / 1000 : 0);
}
function renderTimer() {
  const s = currentSeconds();
  $("timer").textContent = fmtTime(s);
  $("levelFill").style.width = `${Math.min(100, Math.sqrt(state.peakLevel) * 260)}%`;
  state.peakLevel = 0;
  const secs = audioSeconds();
  $("audioLen").textContent = secs ? `${secs.toFixed(1)}s recorded` : "";
  const hi = state.target?.[1] || 120;
  $("timeFill").style.width = `${Math.min(100, (s / (hi * 1.5)) * 100)}%`;
}

/* ------------------------------------------------------------------ analyze */
$("analyzeBtn").onclick = analyze;
async function analyze() {
  stopRecording();
  const transcript = $("transcript").value.trim();
  const hasAudio = audioSeconds() > 1.5;
  const canTranscribe = state.config.transcriber !== "none";
  const minWords = state.mode === READ_ALOUD ? 1 : 3;
  if (transcript.split(/\s+/).filter(Boolean).length < minWords && !(hasAudio && canTranscribe)) {
    $("status").textContent = hasAudio ? "No transcript captured — use Chrome/Edge for live transcription, set up Whisper, or type your answer."
      : "Speak or type a few sentences first.";
    return;
  }
  if (state.customPrompt) { const v = $("promptEdit").value.trim(); if (v) setPromptKeepChain(v); }
  $("analyzeBtn").disabled = true;
  $("status").textContent = hasAudio ? (canTranscribe ? "Transcribing and analyzing your voice…" : "Analyzing your voice…") : "Analyzing…";
  const body = {
    transcript, mode: state.mode, prompt: state.prompt,
    challenge_mode: $("challenge").checked,
    duration_seconds: state.spokeSeconds > 3 ? Math.round(state.spokeSeconds) : null,
    chain_id: state.chainId,
    kind: state.ctx?.kind || "free",
    lesson_id: state.ctx?.kind === "lesson" ? state.ctx.lessonId : null,
    target_text: state.mode === READ_ALOUD ? state.prompt : null,
  };
  if (state.mode === READ_ALOUD && body.kind === "free") body.kind = "read_aloud";
  // one idempotency key per take: re-clicking or a network retry returns the same job (no double charge)
  state.idemKey = state.idemKey || `take-${crypto.randomUUID ? crypto.randomUUID() : Date.now() + "-" + Math.random().toString(36).slice(2)}`;
  try {
    let job;
    try { job = await submitJob(body, hasAudio); } catch (e) {
      if (e.status !== 409) throw e;            // the take was edited after a failed submit → new key
      state.idemKey = `take-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      job = await submitJob(body, hasAudio);
    }
    rememberJob(job.id);
    const done = await pollJob(job.id);
    showJobResult(done);
  } catch (e) {
    $("status").textContent = e.message;
  } finally {
    $("analyzeBtn").disabled = false;
  }
}

/* ------------------------------------------------------------------ analysis jobs (v8.4) */
async function submitJob(body, hasAudio) {
  const headers = { "X-Timezone": TZ, "Idempotency-Key": state.idemKey };
  let resp;
  if (hasAudio) {
    const fd = new FormData();
    fd.append("audio", encodeWav(state.audioChunks, state.audioRate), "take.wav");
    fd.append("payload", JSON.stringify(body));
    resp = await fetch("/api/jobs/audio", { method: "POST", body: fd, headers, credentials: "same-origin" });
  } else {
    resp = await fetch("/api/jobs", { method: "POST", body: JSON.stringify(body), credentials: "same-origin",
      headers: { ...headers, "Content-Type": "application/json" } });
  }
  const data = await resp.json().catch(() => ({}));
  if (resp.status === 401) showAuth("signin", "Your session ended — please sign in again.");
  if (!resp.ok) { const err = new Error(data.detail || `Request failed (${resp.status})`); err.status = resp.status; throw err; }
  return data;
}

function rememberJob(id) { try { id ? localStorage.setItem("tts-active-job", id) : localStorage.removeItem("tts-active-job"); } catch (_) {} }

async function pollJob(id) {
  const labels = { queued: "Queued…", running: "Analyzing — transcribing, measuring and scoring…" };
  let wait = 400;
  for (;;) {
    const job = await api(`/api/jobs/${encodeURIComponent(id)}`);
    if (job.status === "completed" || job.status === "failed") return job;
    $("status").textContent = labels[job.status] || "Working…";
    await new Promise((r) => setTimeout(r, wait));
    wait = Math.min(2000, Math.round(wait * 1.4));
  }
}

function showJobResult(job) {
  rememberJob(null);
  if (job.status === "failed") { $("status").textContent = job.error || "Analysis failed — please try again."; return; }
  const r = job.result;
  if (!r) return;
  if (r.attempt) {
    state.lastAttempt = r.attempt; state.chainId = r.attempt.chain_id;
    $("transcript").value = r.attempt.transcript;   // show exactly what was analyzed (Whisper text if used)
  }
  state.idemKey = null;
  renderAnalysis(r.analysis);
  if (r.attempt) renderVoice(r.analysis.voice, r.attempt.id); else $("voiceCard").classList.add("hidden");
  renderPronunciation(r.analysis);
  renderLanguage(r.analysis);
  renderLessonResult(r.lesson_result, r.program);
  renderFocus(r.analysis);
  syncMore();
  if (r.comparison) renderComparison(r.comparison, r.attempt.chain_id);
  else $("compareCard").classList.add("hidden");
  $("status").textContent = !r.attempt ? "Analyzed (not saved)." : r.attempt.attempt_no > 1
    ? "Compared with your previous take ↓" : "Saved. Now retry — don't copy, re-speak.";
  if (innerWidth < 900) $("resultsCard").scrollIntoView({ behavior: "smooth" });
}

// After a refresh or a dropped connection, pick up the analysis that was still running.
async function resumeActiveJob() {
  let id = null;
  try { id = localStorage.getItem("tts-active-job"); } catch (_) {}
  if (!id) {
    const active = await api("/api/jobs?active=true").catch(() => []);
    id = active[0]?.id;
  }
  if (!id) return;
  showTab("practice");
  $("status").textContent = "Finishing the analysis you started…";
  $("analyzeBtn").disabled = true;
  try { showJobResult(await pollJob(id)); }
  catch (_) { rememberJob(null); $("status").textContent = ""; }
  finally { $("analyzeBtn").disabled = false; }
}
function setPromptKeepChain(p) {
  state.prompt = p; state.customPrompt = false;
  $("promptText").textContent = p; $("promptText").classList.remove("hidden"); $("promptEdit").classList.add("hidden");
  $("customPromptBtn").textContent = "✎ Custom";
}

function renderAnalysis(a) {
  $("emptyState").classList.add("hidden"); $("results").classList.remove("hidden");
  $("warning").textContent = a.warning || ""; $("warning").classList.toggle("hidden", !a.warning);
  $("overall").textContent = a.overall_score;
  const C = 2 * Math.PI * 52, fg = $("ringFg");
  fg.style.strokeDasharray = C; fg.style.strokeDashoffset = C * (1 - a.overall_score / 100);
  $("ring").setAttribute("aria-label", `Overall score ${a.overall_score} out of 100`);

  const ra = a.kind === "read_aloud";
  $("results").classList.toggle("read-aloud", ra);
  if (ra) {
    const p = a.pronunciation;
    $("dims").innerHTML = [["Words recognised", p.accuracy], ["Delivery", a.delivery_score]].map(([l, v]) => `
      <div class="dim"><span>${l}</span><div class="dim-bar"><div style="width:${v}%"></div></div><b>${v}</b></div>`).join("") +
      `<p class="small muted" style="margin:4px 0 0">${p.correct} of ${p.total} words came through cleanly${p.has_confidence ? "" : " · word confidence needs local Whisper"}</p>`;
    const m = a.metrics;
    $("metrics").innerHTML = [[p.correct + "/" + p.total, "clear words"], [p.substituted.length, "swapped"],
      [p.missed.length, "missed"], [p.unclear.length, "unclear"], [m.wpm || "—", "words / min"]]
      .map(([v, l]) => `<div class="metric"><b>${esc(v)}</b><span>${esc(l)}</span></div>`).join("");
    return finishAnalysis(a);
  }
  const labels = state.config.dimensions, s = a.scores;
  const weakest = DIMS.reduce((w, d) => (s[d] < s[w] ? d : w), DIMS[0]);
  const pv = a.provenance || {};
  $("dims").innerHTML = DIMS.map((d) => `
    <div class="dim ${d === weakest ? "weak" : ""}"><span>${esc(labels[d])} ${provBadge(pv.scores?.[d], d)}</span>
      <div class="dim-bar"><div style="width:${s[d]}%"></div></div><b>${s[d]}</b></div>`).join("");

  const m = a.metrics, est = !m.seconds_measured;
  $("metrics").innerHTML = [
    [m.word_count, "words", "measured"],
    [fmtTime(m.seconds), est ? "est. time" : "spoken", est ? "estimated" : "measured"],
    [m.wpm || "—", "words / min", est ? "estimated" : "measured"],
    [m.filler_rate, "fillers / 100w", "measured"],
    [m.hedge_count, "hedges", "measured"],
  ].map(([v, l, k]) => `<div class="metric ${k}" title="${k === "estimated" ? "Estimated from word count (no recording timing)" : "Counted from your transcript or measured from your recording"}">
    <b>${esc(v)}${k === "estimated" ? "<sup>est</sup>" : ""}</b><span>${esc(l)}</span></div>`).join("");
  $("provenanceLine").innerHTML = provenanceLine(pv);
  finishAnalysis(a);
}

/* ------------------------------------------------------------------ focused results (v8.3) */
const fmtTs = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
function evidenceHtml(x) {
  if (!x || !x.quote) return "";
  const ts = x.start != null ? ` <button class="ts" data-t="${x.start}" title="Play this moment">▶ ${fmtTs(x.start)}</button>` : "";
  return `<p class="fc-quote"><q>${esc(x.quote)}</q>${ts}</p>`;
}
function renderFocus(a) {
  const el = $("focusCard"), f = a.focus;
  if (!f) { el.classList.add("hidden"); el.innerHTML = ""; $("nextChallenge").classList.remove("hidden");
    $("retryBtn").classList.remove("hidden"); return; }
  $("retryBtn").classList.add("hidden");          // "Retry now" in the focus card is the one retry button
  el.classList.remove("hidden");
  el.innerHTML = `
    <div class="fc-row good"><span class="fc-ico" aria-hidden="true">✓</span><div>
      <span class="eyebrow">Keep doing</span><p>${esc(f.strength.text)}</p>${evidenceHtml(f.strength)}</div></div>
    <div class="fc-row pri"><span class="fc-ico" aria-hidden="true">!</span><div>
      <span class="eyebrow">Fix first</span><p><b>${esc(f.priority.text)}</b></p>${evidenceHtml(f.priority)}</div></div>
    <div class="fc-row retry"><span class="fc-ico" aria-hidden="true">↻</span><div>
      <span class="eyebrow">On your retry</span><p>${esc(f.retry)}</p>
      <button class="primary sm" id="fcRetry">🔁 Retry now</button></div></div>`;
  $("fcRetry").onclick = () => { startRetry(); $("status").textContent = `Focus: ${f.retry}`; };
  // the retry instruction replaces the generic "next challenge" text; the buttons below stay
  $("nextChallenge").classList.add("hidden"); $("nextLabel").textContent = "Keep going";
}
// ▶ timestamp chips anywhere in the results jump the recording to that moment
document.addEventListener("click", (e) => {
  const b = e.target.closest(".ts[data-t]");
  if (!b) return;
  const player = $("player");
  if (!player.src) return;
  $("moreVoice").open = true;
  player.currentTime = +b.dataset.t; player.play().catch(() => {});
});
function syncMore() {
  const hasContent = { moreVoice: !$("voiceCard").classList.contains("hidden"),
    moreLanguage: !$("languageCard").classList.contains("hidden") };
  Object.entries(hasContent).forEach(([id, on]) => $(id).classList.toggle("hidden", !on));
}
document.querySelectorAll("details.more").forEach((d) => {
  try { d.open = localStorage.getItem(`tts-more-${d.dataset.key}`) === "1"; } catch (_) {}
  d.addEventListener("toggle", () => { try { localStorage.setItem(`tts-more-${d.dataset.key}`, d.open ? "1" : "0"); } catch (_) {} });
});

/* ------------------------------------------------------------------ scoring provenance (v8.2) */
function provBadge(src, dim) {
  if (src === "ai") return `<span class="pb ai" title="Judged by the AI coach">AI</span>`;
  if (dim === "delivery") return `<span class="pb est" title="Built-in formula over measured signals: fillers, hedges, pace, pauses and pitch">formula</span>`;
  return `<span class="pb est" title="Built-in heuristic estimate — add an API key for AI judgement">est.</span>`;
}
function provenanceLine(pv) {
  if (!pv.scoring_version) return "";
  return `Scored by ${esc(pv.scorer || pv.scoring_version)} · transcript: ${esc(pv.transcript_source || "unknown")}` +
    `${pv.rubric_version ? ` · rubric ${esc(pv.rubric_version)}` : ""}<br>` +
    `<span class="pb ai">AI</span> judged by the AI coach · <span class="pb est">est.</span> built-in estimate · ` +
    `numbers without a tag are measured from your transcript or recording.`;
}

function finishAnalysis(a) {
  const f = Object.entries(a.metrics.fillers || {});
  $("fillerChips").innerHTML = f.length
    ? f.map(([k, v]) => `<span class="chip">“${esc(k)}” ×${v}</span>`).join("")
    : `<span class="chip">No fillers detected 👏</span>`;

  const li = (xs) => (xs || []).map((x) => `<li>${esc(x)}</li>`).join("");
  $("strengths").innerHTML = li(a.strengths);
  $("improvements").innerHTML = li(a.improvements);
  const icon = { present: "✓", weak: "~", missing: "✗" };
  $("structureMap").innerHTML = (a.structure_map || []).map((x) => `
    <div class="sm-item ${esc(x.status)}"><span class="ico" aria-label="${esc(x.status)}">${icon[x.status] || "~"}</span>
      <b>${esc(x.label)}</b><span class="note">${esc(x.note)}</span></div>`).join("");

  renderReview(a);
  $("coachTitle").textContent = a.challenge_mode ? "🧠 Challenge Mode — find the fix yourself" : "💡 Coach";
  $("coachHint").textContent = a.coaching_hint || "";
  $("coachQuestions").innerHTML = li(a.coaching_questions);
  const outline = a.suggested_outline || [];
  $("outlineWrap").classList.toggle("hidden", a.challenge_mode || !outline.length);
  $("outline").innerHTML = li(outline);
  $("nextChallenge").textContent = a.next_challenge || "";
}

/* ------------------------------------------------------------------ three-angle review (v8) */
function renderReview(a) {
  const el = $("reviewCard"), r = a.review;
  const legacy = ["legacyFeedback", "structureWrap", "coachBox"];
  if (!r) { el.classList.add("hidden"); legacy.forEach((id) => $(id).classList.remove("hidden")); return; }
  legacy.forEach((id) => $(id).classList.add("hidden"));
  const ev = r.evaluator, co = r.coach, me = r.mentor, parts = r.breakdown?.parts || [];
  const q = (x) => x ? `<q>${esc(x)}</q>` : "";
  const tsb = (x) => x && x.start != null ? ` <button class="ts" data-t="${x.start}" title="Play this moment">▶ ${fmtTs(x.start)}</button>` : "";
  const quoteList = (xs, empty) => xs.length ? `<ul class="rv-quotes">${xs.map((x) =>
    `<li>${x.quote ? `${q(x.quote)}${tsb(x)}<br>` : ""}<span>${esc(x.why)}</span></li>`).join("")}</ul>` : `<p class="small muted">${empty}</p>`;
  const icon = { present: "✓", weak: "~", missing: "✗" };
  const outline = a.suggested_outline || [];
  el.classList.remove("hidden");
  el.innerHTML = `
    <section class="rv-sec rv-eval v-${esc(ev.verdict)}">
      <div class="rv-h"><span class="rv-who">🎯 ${esc(ev.role)}</span>
        <span class="rv-badge">${esc(ev.verdict_label)}</span></div>
      <p class="small muted rv-qn">${esc(ev.question)}</p>
      <p class="rv-sum">“${esc(ev.summary)}”</p>
      <div class="rv-2col">
        <div><div class="eyebrow">What landed</div>${quoteList(ev.landed, "Nothing stood out yet — the coach's fixes below will change that.")}</div>
        <div><div class="eyebrow">What raised a doubt</div>${quoteList(ev.doubts, "Nothing — a clean answer.")}</div>
      </div>
      ${ev.to_strong_yes?.length ? `<div class="eyebrow">To get to “${esc(ev.verdict === "strong_yes" ? "even better" : (ev.role === "Interviewer" ? "Strong yes" : "the top verdict"))}”</div>
        <ul class="small">${ev.to_strong_yes.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
      <div class="rv-follow"><div><span class="eyebrow">They'd likely ask next</span><p>${esc(ev.follow_up)}</p></div>
        <button class="ghost sm" id="rvFollow">🎙️ Answer it</button></div>
    </section>

    ${renderCorrections(r.corrections, a.challenge_mode)}

    <section class="rv-sec">
      <div class="rv-h"><span class="rv-who">🧱 Your answer, part by part</span><span class="small muted">${esc(a.framework_name)}</span></div>
      <div class="rv-parts">${parts.map((p) => `<div class="rv-part ${esc(p.status)}">
        <span class="ico" aria-label="${esc(p.status)}">${icon[p.status] || "~"}</span><b>${esc(p.label)}</b>
        <span>${p.quote ? q(p.quote) + tsb(p) : `<span class="muted">${p.status === "weak" ? "Unclear — couldn't match a sentence" : "Not in your answer"}</span>`}${p.note ? `<br><span class="small muted">${esc(p.note)}</span>` : ""}</span></div>`).join("")}</div>
      ${r.breakdown?.order_note ? `<p class="small rv-note">↕ ${esc(r.breakdown.order_note)}</p>` : ""}
    </section>

    <section class="rv-sec rv-coach">
      <div class="rv-h"><span class="rv-who">🏋️ Coach</span><span class="small muted">${a.challenge_mode ? "Challenge Mode — find the fix yourself" : "before your next take"}</span></div>
      ${co.fixes?.length ? `<div class="eyebrow">Top fixes</div><ol class="rv-fixes">${co.fixes.map((f) => `<li>
        ${f.quote ? `<span class="small muted">You said</span> ${q(f.quote)}${tsb(f)}<br>` : ""}<b>${esc(f.problem)}</b><br><span>→ ${esc(f.fix)}</span></li>`).join("")}</ol>` : ""}
      ${a.challenge_mode && a.coaching_questions?.length ? `<div class="eyebrow">Ask yourself</div><ul class="small">${a.coaching_questions.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
      ${co.do_next?.length ? `<div class="eyebrow">On your next take</div><ul class="rv-next">${co.do_next.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
      <div class="rv-actions"><button class="primary sm" id="rvRetry">🔁 Retry with these fixes</button></div>
      ${co.drill ? `<details class="rv-drill"><summary>🎯 2-minute drill: <b>${esc(co.drill.title)}</b></summary>
        <ol class="small">${co.drill.steps.map((x) => `<li>${esc(x)}</li>`).join("")}</ol></details>` : ""}
      ${co.stronger_version ? `<details class="rv-drill"><summary>✨ Hear a stronger version of <i>your</i> answer</summary>
        <p class="rv-strong">${esc(co.stronger_version)}</p>
        <button class="ghost sm" id="rvHear">▶ Listen</button>
        <p class="small muted">Same facts, better shape. Don't memorise it — notice what changed, then say it your way.</p></details>`
        : !a.challenge_mode && outline.length ? `<details class="rv-drill"><summary>🦴 Skeleton built from your own words</summary>
        <ul class="small">${outline.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
        <p class="small muted">Add an API key for a full rewritten version of your answer.</p></details>` : ""}
    </section>

    <section class="rv-sec rv-mentor">
      <div class="rv-h"><span class="rv-who">🧭 Mentor</span><span class="small muted">${me.based_on ? `based on your last ${me.based_on} takes` : "the bigger picture"}</span></div>
      ${me.pattern ? `<p><b>Pattern:</b> ${esc(me.pattern)}</p>` : ""}
      ${me.improving ? `<p><b>Improving:</b> ${esc(me.improving)}</p>` : ""}
      <p><b>This week's habit:</b> ${esc(me.habit)}</p>
      ${me.note ? `<p class="small muted">${esc(me.note)}</p>` : ""}
    </section>
    <p class="small muted rv-src">${r.source === "ai" ? "Review written by your AI coach." : "Review from the built-in coach — add an API key for deeper, AI-written feedback."}
      Every quote was checked against your transcript${r.evidence?.dropped ? ` (${r.evidence.dropped} unverifiable quote${r.evidence.dropped > 1 ? "s" : ""} removed)` : ""}${r.evidence?.timestamps ? "; ▶ times come from Whisper's word timings" : ""}.</p>`;
  $("rvFollow").onclick = () => {
    state.chainId = null; state.takeNo = 1;
    practiceWith({ mode: a.mode in state.modes ? a.mode : state.mode, prompt: ev.follow_up });
    $("transcript").value = ""; $("status").textContent = "Follow-up question loaded — hit “Start speaking”.";
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  $("rvRetry").onclick = () => {
    startRetry();
    if (co.do_next?.length) $("status").textContent = `Focus: ${co.do_next[0]}`;
  };
  if ($("rvHear")) $("rvHear").onclick = () => speak(co.stronger_version, 0.95);
  if ($("rvHearFixed")) $("rvHearFixed").onclick = () => speak(r.corrections.corrected_full, 0.95);
}

function renderCorrections(c, challenge) {
  const items = c?.items || [];
  if (!items.length) return `<section class="rv-sec rv-fix"><div class="rv-h"><span class="rv-who">✍️ Corrections</span></div>
    <p class="small muted">No corrections needed — your sentences came through clean.</p></section>`;
  return `<section class="rv-sec rv-fix">
    <div class="rv-h"><span class="rv-who">✍️ Corrections</span><span class="small muted">${challenge ? "find the fix yourself" : "what you said → how to say it"}</span></div>
    <ol class="rv-corr">${items.map((x) => `<li>
      <div class="rv-said"><span class="eyebrow">You said</span><span><q>${esc(x.original)}</q>${x.start != null ? ` <button class="ts" data-t="${x.start}">▶ ${fmtTs(x.start)}</button>` : ""}</span></div>
      ${x.corrected ? `<div class="rv-better"><span class="eyebrow">Say</span><span>${esc(x.corrected)}</span></div>` : ""}
      ${(x.changes || []).length || (x.notes || []).length ? `<ul class="rv-why">${[...(x.changes || []), ...(x.notes || [])]
        .map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}</li>`).join("")}</ol>
    ${c.corrected_full ? `<details class="rv-drill"><summary>📝 Your whole answer, corrected</summary>
      <p class="rv-strong">${esc(c.corrected_full)}</p><button class="ghost sm" id="rvHearFixed">▶ Listen</button>
      <p class="small muted">Same words and meaning — just fixed. Read it aloud once, then answer again from memory.</p></details>` : ""}
  </section>`;
}

function deltaCell(d) {
  if (d === null || d === undefined) return `<td class="delta flat">—</td>`;
  const cls = d >= 3 ? "up" : d <= -3 ? "down" : "flat";
  const arrow = d >= 3 ? "▲" : d <= -3 ? "▼" : "•";
  return `<td class="delta ${cls}">${arrow} ${d > 0 ? "+" : ""}${d}</td>`;
}

function fillerDeltaCell(d) {  // fewer fillers is better
  if (d === null || d === undefined) return `<td class="delta flat">—</td>`;
  const cls = d < 0 ? "up" : d > 0 ? "down" : "flat";
  return `<td class="delta ${cls}">${d < 0 ? "▼" : d > 0 ? "▲" : "•"} ${d > 0 ? "+" : ""}${d}</td>`;
}

async function renderComparison(c, chainId) {
  $("compareCard").classList.remove("hidden");
  const v = $("verdict"); v.className = `verdict ${c.verdict}`;
  v.textContent = { improved: "▲ Improved", regressed: "▼ Dipped", steady: "• Steady", incompatible: "≠ Not comparable" }[c.verdict];
  $("compareSummary").textContent = c.summary;
  const cmp = c.compatibility || { scores: true, metrics: true };
  $("compareSummary").classList.toggle("cmp-warn", !cmp.scores);
  const labels = { overall: "Overall", ...state.config.dimensions };
  $("compareTable").innerHTML =
    `<tr><th>Skill</th><th>Take ${c.base_attempt}</th><th>Take ${c.current_attempt}</th><th>Change</th></tr>` +
    ["overall", ...DIMS].map((d) => `<tr><td>${d === "overall" ? "<b>Overall</b>" : esc(labels[d])}</td>
      <td>${c.base_scores[d] ?? "—"}</td><td><b>${c.current_scores[d] ?? "—"}</b></td>${deltaCell(c.deltas[d])}</tr>`).join("") +
    `<tr><td>Filler words</td><td>${c.base_fillers}</td><td><b>${c.current_fillers}</b></td>${fillerDeltaCell(c.metric_deltas.filler_count)}</tr>` +
    (cmp.metric_reason ? `<tr><td colspan="4" class="small muted">${esc(cmp.metric_reason)}</td></tr>` : "");
  const li = (xs) => (xs || []).map((x) => `<li>${esc(x)}</li>`).join("");
  $("cmpBetter").innerHTML = li(c.what_improved);
  $("cmpWorse").innerHTML = li(c.still_to_work_on);
  $("takesSpark").classList.toggle("hidden", !cmp.scores);   // bar heights imply a comparison — hide if unfair
  try {
    const chain = await api(`/api/chain/${chainId}`);
    $("takesSpark").innerHTML = chain.map((t, i) =>
      `<div class="tk ${i === chain.length - 1 ? "cur" : ""}" style="height:${Math.max(6, t.overall || 0) * 0.6}%"
        title="Take ${t.attempt_no}: ${t.overall}"><span>${t.overall}</span></div>`).join("");
  } catch (_) {}
}

/* ------------------------------------------------------------------ progress */
const tip = $("tooltip");
function showTip(html, ev) {
  tip.innerHTML = html; tip.classList.remove("hidden");
  const x = Math.min(ev.clientX + 12, innerWidth - tip.offsetWidth - 8);
  tip.style.left = `${x}px`; tip.style.top = `${ev.clientY - tip.offsetHeight - 10}px`;
}
const hideTip = () => tip.classList.add("hidden");

async function loadProgress() {
  const p = await api("/api/progress");
  renderRecommendation(p.recommendation);
  renderLangProgress(p.language);
  if (!p.total_attempts) {
    $("kpis").innerHTML = ""; $("trendChart").innerHTML = `<p class="muted">No practice yet.</p>`;
    $("dimChart").innerHTML = ""; $("modeTable").innerHTML = ""; $("fillerBars").innerHTML = "";
    return;
  }
  const d = p.avg_overall_last10 != null && p.avg_overall_prev10 != null ? p.avg_overall_last10 - p.avg_overall_prev10 : null;
  const k = [
    [p.avg_overall_last10 ?? "—", "Avg score (last 10)",
      d == null ? "" : `<span class="delta ${d >= 0 ? "up" : "down"}">${d >= 0 ? "▲" : "▼"} ${Math.abs(d).toFixed(1)} vs previous 10</span>`],
    [p.total_attempts, "Takes recorded", `<span class="muted">${p.total_prompts} prompts</span>`],
    [p.avg_retry_gain == null ? "—" : `${p.avg_retry_gain > 0 ? "+" : ""}${p.avg_retry_gain}`, "Avg gain from retrying",
      `<span class="muted">${p.retry_chains} retried prompts</span>`],
    [p.streak_days, "Day streak", `<span class="muted">${p.practice_days} practice days</span>`],
    [p.best_overall ?? "—", "Best score", `<span class="muted">${p.avg_filler_rate ?? "—"} fillers/100w lately</span>`],
  ];
  $("kpis").innerHTML = k.map(([v, l, s]) => `<div class="kpi"><div class="v">${esc(v)}</div><div class="l">${l}</div><div class="d">${s}</div></div>`).join("");
  drawTrend(p.trend);
  $("scoringNote").textContent = p.scoring?.note || "";
  $("scoringNote").classList.toggle("hidden", !p.scoring?.note);
  drawDims(p.dimension_avgs, p.dimension_avgs_prev, p.dimension_labels);
  $("modeTable").innerHTML = `<tr><th>Scenario</th><th>Takes</th><th>Avg</th><th>Best</th></tr>` +
    Object.entries(p.by_mode).sort((a, b) => b[1].count - a[1].count)
      .map(([m, v]) => `<tr><td>${esc(m)}</td><td>${v.count}</td><td>${v.avg}</td><td>${v.best}</td></tr>`).join("");
  const maxF = Math.max(1, ...p.top_fillers.map((x) => x[1]));
  $("fillerBars").innerHTML = p.top_fillers.length
    ? p.top_fillers.map(([w, n]) => `<div class="fbar"><span>“${esc(w)}”</span><div class="t"><div style="width:${(n / maxF) * 100}%"></div></div><b>${n}</b></div>`).join("")
    : `<p class="muted">No fillers in your recent takes. 👏</p>`;
}

function renderRecommendation(r) {
  const steps = r.steps.map((s) => `<li>${esc(s)}</li>`).join("");
  const tips = (r.secondary_tips || []).map((s) => `<li>${esc(s)}</li>`).join("");
  $("recCard").innerHTML = `
    <div class="rec-grid">
      <div>
        <span class="eyebrow">Your next exercise${r.dimension_label ? ` · targets ${esc(r.dimension_label)}` : ""}</span>
        <div class="rec-title">${esc(r.title)}</div>
        <p style="margin:0">${esc(r.why)}</p>
        ${r.evidence ? `<div class="rec-evidence">📊 ${esc(r.evidence)}</div>` : ""}
        ${tips ? `<ul class="small muted">${tips}</ul>` : ""}
      </div>
      <div>
        <span class="eyebrow">${esc(r.mode)} · prompt</span>
        <p style="font-weight:650;margin:4px 0 8px">${esc(r.prompt)}</p>
        <ol class="small">${steps}</ol>
        <p class="small"><b>Constraint:</b> ${esc(r.constraint)}</p>
        <button class="primary" id="startRec">▶ Start this exercise</button>
      </div>
    </div>`;
  $("startRec").onclick = () => startRecommended(r);
}

function startRecommended(r) {
  practiceWith({ mode: r.mode, prompt: r.prompt, challenge: r.challenge_mode,
    ctx: { kind: "review", title: `Review: ${r.title}`, constraint: r.constraint, steps: r.steps } });
}

function drawTrend(points) {
  const el = $("trendChart");
  if (!points.length) { el.innerHTML = ""; return; }
  const W = 560, H = 230, L = 30, R = 10, T = 12, B = 26;
  const n = points.length;
  const x = (i) => L + (n === 1 ? (W - L - R) / 2 : (i * (W - L - R)) / (n - 1));
  const y = (v) => T + (1 - v / 100) * (H - T - B);
  let g = "";
  [0, 25, 50, 75, 100].forEach((v) => (g += `<line class="grid" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${v}</text>`));
  // the line joins only takes from the current scorer; others are faded dots (not comparable)
  const path = points.map((p, i) => [p, i]).filter(([p]) => p.comparable !== false)
    .map(([p, i], j) => `${j ? "L" : "M"}${x(i).toFixed(1)},${y(p.overall).toFixed(1)}`).join("");
  const dots = points.map((p, i) => `<circle class="dot ${p.attempt_no > 1 ? "retry" : ""} ${p.comparable === false ? "faded" : ""}" cx="${x(i)}" cy="${y(p.overall)}" r="4.5"/>`).join("");
  const step = (W - L - R) / Math.max(1, n - 1);
  const hits = points.map((p, i) => `<rect class="hit" data-i="${i}" x="${x(i) - step / 2}" y="${T}" width="${Math.max(step, 12)}" height="${H - T - B}"/>`).join("");
  const first = new Date(points[0].date), last = new Date(points[n - 1].date);
  const fd = (d) => d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Overall score trend across ${n} takes">
    ${g}<path class="line" d="${path}"/>${dots}
    <text x="${L}" y="${H - 6}">${fd(first)}</text><text x="${W - R}" y="${H - 6}" text-anchor="end">${fd(last)}</text>
    ${hits}</svg>`;
  el.querySelectorAll(".hit").forEach((h) => {
    h.onmousemove = (ev) => {
      const p = points[+h.dataset.i];
      showTip(`<b>${p.overall}/100</b><br>${esc(p.mode)} · take ${p.attempt_no}<br>${new Date(p.date).toLocaleString()}` +
        (p.comparable === false ? `<br><i>Different scorer — not averaged</i>` : ""), ev);
    };
    h.onmouseleave = hideTip;
  });
}

function drawDims(cur, prev, labels) {
  const el = $("dimChart");
  const hasPrev = DIMS.some((d) => prev[d] != null);
  const W = 560, rowH = 40, L = 150, R = 40, H = DIMS.length * rowH + 6;
  const x = (v) => ((v || 0) / 100) * (W - L - R);
  let s = "";
  DIMS.forEach((d, i) => {
    const y0 = i * rowH + 6;
    s += `<text class="lbl" x="0" y="${y0 + 15}">${esc(labels[d])}</text>`;
    s += `<line class="grid" x1="${L}" x2="${W - R}" y1="${y0 + 30}" y2="${y0 + 30}" />`;
    if (cur[d] != null) {
      s += `<rect class="bar-cur" x="${L}" y="${y0}" width="${x(cur[d])}" height="12" rx="3"/>`;
      s += `<text class="val" x="${L + x(cur[d]) + 6}" y="${y0 + 10}">${Math.round(cur[d])}</text>`;
    }
    if (hasPrev && prev[d] != null) {
      s += `<rect class="bar-prev" x="${L}" y="${y0 + 14}" width="${x(prev[d])}" height="8" rx="3"/>`;
    }
  });
  el.innerHTML = `<div class="legend"><span><i style="background:var(--accent)"></i>Last 10</span>${hasPrev ? `<span><i style="background:var(--prev)"></i>Previous 10</span>` : ""}</div>
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Average score per skill">${s}</svg>`;
}

/* ------------------------------------------------------------------ history */
$("historyMode").onchange = loadHistory;
async function loadHistory() {
  const mode = $("historyMode").value;
  const rows = await api(`/api/history?limit=400${mode ? `&mode=${encodeURIComponent(mode)}` : ""}`);
  const el = $("historyList");
  if (!rows.length) { el.innerHTML = `<p class="muted">No sessions yet.</p>`; return; }
  const chains = new Map();
  rows.forEach((r) => { if (!chains.has(r.chain_id)) chains.set(r.chain_id, []); chains.get(r.chain_id).push(r); });
  el.innerHTML = [...chains.values()].map((takes) => {
    takes.sort((a, b) => a.attempt_no - b.attempt_no);
    const f = takes[0], l = takes[takes.length - 1];
    const gain = takes.length > 1 && f.overall != null && l.overall != null ? l.overall - f.overall : null;
    return `<div class="hchain">
      <div class="hchain-h"><div><span class="tag">${esc(f.mode)}</span>
        <span class="p">${esc(f.prompt || "Free practice")}</span></div>
        <div class="small muted">${new Date(f.created_at).toLocaleString()} · ${takes.length} take${takes.length > 1 ? "s" : ""}
        ${gain != null ? ` · <span class="delta ${gain >= 0 ? "up" : "down"}">${gain >= 0 ? "▲ +" : "▼ "}${gain}</span>` : ""}</div></div>
      ${takes.map((t) => historyAttempt(t)).join("")}</div>`;
  }).join("");
  el.querySelectorAll("[data-del]").forEach((b) => (b.onclick = async (e) => {
    e.preventDefault();
    if (!confirm("Delete this take permanently?")) return;
    await api(`/api/attempts/${b.dataset.del}`, { method: "DELETE" }); loadHistory();
  }));
}

function historyAttempt(t) {
  const a = t.analysis || {};
  const s = a.scores || {};
  const dims = DIMS.filter((d) => s[d] != null).map((d) => `${state.config.dimensions[d]}: <b>${s[d]}</b>`).join(" · ");
  const li = (xs) => (xs || []).map((x) => `<li>${esc(x)}</li>`).join("");
  return `<details class="hattempt"><summary>
      <span class="score-pill">${t.overall ?? "—"}</span><b>Take ${t.attempt_no}</b>
      ${t.challenge_mode ? `<span class="tag">Challenge</span>` : ""}
      <span class="small muted">${t.word_count ?? "?"} words · ${t.filler_count ?? 0} fillers · ${esc(t.provider || "")}</span></summary>
    <div class="body">
      <p class="transcript-quote">“${esc(t.transcript)}”</p>
      ${t.audio_path ? `<audio controls preload="none" src="/api/audio/${esc(t.id)}" style="width:100%;height:34px"></audio>` : ""}
      ${a.voice ? `<p class="small muted">${esc(voiceSummary(a.voice))}</p>` : ""}
      ${dims ? `<p class="small">${dims}</p>` : ""}
      ${a.focus ? `<ul class="small hfocus"><li>✓ ${esc(a.focus.strength.text)}</li><li><b>! ${esc(a.focus.priority.text)}</b></li>
        <li>↻ ${esc(a.focus.retry)}</li></ul>` : `<div class="split"><div><h3>What worked</h3><ul class="small">${li(a.strengths)}</ul></div>
      <div><h3>What to improve</h3><ul class="small">${li(a.improvements)}</ul></div></div>`}
      ${t.scoring_version ? `<p class="small muted">Scored: ${esc(t.scoring_version)}${t.model ? ` · ${esc(t.model)}` : ""}</p>` : ""}
      <p style="margin-top:12px"><button class="danger" data-del="${esc(t.id)}">Delete take</button></p>
    </div></details>`;
}

/* ------------------------------------------------------------------ real audio capture (v4) */
const WORKLET_SRC = `class TTSRec extends AudioWorkletProcessor {
  process(inputs) { const ch = inputs[0] && inputs[0][0]; if (ch) this.port.postMessage(ch.slice(0)); return true; }
}
registerProcessor("tts-rec", TTSRec);`;

async function startCapture() {
  const stream = await navigator.mediaDevices.getUserMedia({
    // AGC off on purpose: it would hide the volume drop we want to measure.
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: false },
  });
  const ctx = new AudioContext();
  const url = URL.createObjectURL(new Blob([WORKLET_SRC], { type: "application/javascript" }));
  await ctx.audioWorklet.addModule(url);
  URL.revokeObjectURL(url);
  const src = ctx.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(ctx, "tts-rec");
  const mute = ctx.createGain(); mute.gain.value = 0;      // keep the node pulled without playing back
  node.port.onmessage = (e) => {
    const d = e.data; state.audioChunks.push(d);
    let sum = 0; for (let i = 0; i < d.length; i++) sum += d[i] * d[i];
    state.peakLevel = Math.max(state.peakLevel, sum / d.length);
  };
  src.connect(node); node.connect(mute); mute.connect(ctx.destination);
  state.audioRate = ctx.sampleRate;
  state.cap = { stream, ctx, src, node, mute };
  $("micLabel").textContent = "🔴 Recording audio";
}

function stopCapture() {
  const c = state.cap; if (!c) return;
  try { c.src.disconnect(); c.node.disconnect(); c.mute.disconnect(); } catch (_) {}
  c.stream.getTracks().forEach((t) => t.stop());
  c.ctx.close();
  state.cap = null;
  $("micLabel").textContent = "🎤 Mic off"; $("levelFill").style.width = "0";
}

function audioSeconds() {
  let n = 0; for (const c of state.audioChunks) n += c.length;
  return n / (state.audioRate || 48000);
}

function encodeWav(chunks, rate, outRate = 16000) {
  let n = 0; for (const c of chunks) n += c.length;
  const pcm = new Float32Array(n); let o = 0;
  for (const c of chunks) { pcm.set(c, o); o += c.length; }
  const ratio = rate / outRate, outLen = Math.floor(n / ratio);
  const buf = new ArrayBuffer(44 + outLen * 2), v = new DataView(buf);
  const w = (p, s) => { for (let i = 0; i < s.length; i++) v.setUint8(p + i, s.charCodeAt(i)); };
  w(0, "RIFF"); v.setUint32(4, 36 + outLen * 2, true); w(8, "WAVE"); w(12, "fmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, outRate, true); v.setUint32(28, outRate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  w(36, "data"); v.setUint32(40, outLen * 2, true);
  for (let i = 0; i < outLen; i++) {
    // average the source samples that fall into this output sample (simple anti-aliasing)
    const a = Math.floor(i * ratio), b = Math.max(a + 1, Math.floor((i + 1) * ratio));
    let sum = 0; for (let j = a; j < b && j < n; j++) sum += pcm[j];
    const x = Math.max(-1, Math.min(1, sum / (b - a)));
    v.setInt16(44 + i * 2, x < 0 ? x * 0x8000 : x * 0x7fff, true);
  }
  return new Blob([buf], { type: "audio/wav" });
}

/* ------------------------------------------------------------------ voice report (v4) */
function voiceSummary(v) {
  const p = v.pitch || {};
  return [
    v.speaking_rate_wpm ? `${v.speaking_rate_wpm} wpm overall (${v.articulation_rate_wpm} while talking)` : null,
    `${v.pause_count} pauses, ${v.stalls} stall${v.stalls === 1 ? "" : "s"}, longest ${v.longest_pause}s`,
    p.range_semitones != null ? `pitch range ${p.range_semitones} semitones (${p.label})` : null,
    v.volume.trails_off ? `volume drops ${v.volume.trailing_drop_db} dB at the end` : "volume steady",
    `${Math.round(v.silence_ratio * 100)}% silence`,
  ].filter(Boolean).join(" · ");
}

function renderVoice(v, attemptId) {
  const card = $("voiceCard");
  if (!v) { card.classList.add("hidden"); return; }
  card.classList.remove("hidden");
  const whisper = v.timestamped_words;
  $("transcriptSource").textContent = whisper ? `Transcript: ${v.transcript_source} (verbatim)` : "Transcript: browser STT — fillers may be undercounted";
  $("player").src = `/api/audio/${attemptId}`;
  drawWaveform(v);
  const labels = { pace: "Pace", pauses: "Pauses", pitch_variety: "Pitch variety", volume: "Volume" };
  $("voiceBars").innerHTML = Object.entries(labels).map(([k, l]) => {
    const val = v.scores?.[k];
    return `<div class="dim"><span>${l}</span><div class="dim-bar"><div style="width:${val ?? 0}%"></div></div><b>${val ?? "—"}</b></div>`;
  }).join("");
  drawPace(v.pace || []);
  drawPitch(v.pitch?.contour || [], v.window_seconds);
  $("voiceSummary").textContent = voiceSummary(v);
  const q = v.recording_quality?.note;
  $("voiceQuality").textContent = q || ""; $("voiceQuality").classList.toggle("hidden", !q);
}

function drawWaveform(v) {
  const W = 600, H = 90, mid = 40, env = v.envelope || [], dur = v.duration;
  const x = (t) => (t / dur) * W;
  const bw = W / Math.max(1, env.length);
  const bars = env.map((e, i) => `<rect class="wbar" x="${(i * bw).toFixed(1)}" y="${(mid - e * 36).toFixed(1)}" width="${Math.max(0.6, bw - 0.4).toFixed(1)}" height="${Math.max(0.8, e * 72).toFixed(1)}"/>`).join("");
  const pauses = (v.pauses || []).map((p) => `<rect class="p-${p.kind}" x="${x(p.start)}" y="0" width="${Math.max(1, x(p.end) - x(p.start))}" height="${H - 12}">
      <title>${p.duration}s pause${p.after_word ? ` after “${esc(p.after_word)}”` : ""}</title></rect>
      ${p.kind === "stall" ? `<text class="pl" x="${x(p.start) + 2}" y="10">${p.duration}s</text>` : ""}`).join("");
  const ticks = []; const step = dur > 60 ? 15 : dur > 20 ? 5 : 2;
  for (let t = 0; t <= dur; t += step) ticks.push(`<text class="axis" x="${x(t)}" y="${H - 1}" text-anchor="${t === 0 ? "start" : "middle"}">${fmtTime(t)}</text>`);
  $("waveform").innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Waveform with ${v.pause_count} pauses">
    ${pauses}${bars}${ticks.join("")}<line class="head" id="playhead" x1="-2" x2="-2" y1="0" y2="${H - 12}"/></svg>`;
  const player = $("player");
  $("waveform").onclick = (e) => {
    const r = $("waveform").getBoundingClientRect();
    player.currentTime = ((e.clientX - r.left) / r.width) * dur; player.play().catch(() => {});
  };
  player.ontimeupdate = () => {
    const ph = document.getElementById("playhead"); if (!ph) return;
    const px = x(player.currentTime); ph.setAttribute("x1", px); ph.setAttribute("x2", px);
  };
}

function drawPace(pace) {
  const el = $("paceChart");
  if (!pace.length) { el.innerHTML = `<p class="small muted">Too short to chart.</p>`; return; }
  const W = 300, H = 60, max = 230, y = (v) => H - 10 - (Math.min(v, max) / max) * (H - 14);
  const bw = W / pace.length;
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Words per minute every 10 seconds">
    <rect class="band" x="0" y="${y(165)}" width="${W}" height="${y(130) - y(165)}"><title>130–165 wpm target</title></rect>
    ${pace.map((p, i) => `<rect class="pb" x="${i * bw + 2}" y="${y(p.wpm)}" width="${Math.max(2, bw - 4)}" height="${H - 10 - y(p.wpm)}" rx="2"><title>${p.t}s: ${p.wpm} wpm</title></rect>`).join("")}
    <text x="2" y="${H - 1}">green band = 130–165 wpm</text></svg>`;
}

function drawPitch(contour, dur) {
  const el = $("pitchChart");
  if (contour.length < 5) { el.innerHTML = `<p class="small muted">Not enough voiced audio.</p>`; return; }
  const W = 300, H = 60, y = (s) => H / 2 - (Math.max(-8, Math.min(8, s)) / 8) * (H / 2 - 4);
  const T = dur || contour[contour.length - 1][0] || 1;
  const pts = contour.map(([t, s]) => `${((t / T) * W).toFixed(1)},${y(s).toFixed(1)}`).join(" ");
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Pitch contour in semitones">
    <line class="zero" x1="0" x2="${W}" y1="${H / 2}" y2="${H / 2}"/><polyline class="pl2" points="${pts}"/>
    <text x="2" y="9">+8 st</text><text x="2" y="${H - 2}">−8 st</text></svg>`;
}

/* ------------------------------------------------------------------ learn (v3) */
function renderLessonResult(lr, program) {
  const el = $("lessonResult");
  const c = state.ctx;
  if (!lr) {
    if (c && (c.kind === "warmup" || c.kind === "review")) {
      el.className = "lesson-result pass";
      el.innerHTML = `<h4>✓ ${c.kind === "warmup" ? "Warm-up" : "Review"} done for today</h4>
        <button class="ghost sm" id="lrToday">Back to Today</button>`;
      $("lrToday").onclick = () => showTab("learn");
    } else { el.className = "lesson-result hidden"; el.innerHTML = ""; }
    return;
  }
  const lesson = state.lesson;
  const day = lesson?.program?.day;
  const milestone = lr.passed && program ? (program.milestones || []).find((m) => m.day === day) : null;
  el.className = `lesson-result ${lr.passed ? "pass" : "fail"}`;
  el.innerHTML = `<h4>${lr.passed ? (day ? `✓ Day ${day} of ${program?.total ?? 28} complete` : "✓ Lesson passed") : "Not yet — retry with the framework in mind"}</h4>
    ${lr.passed && program ? `<p class="small" style="margin:0 0 8px">${program.streak ? `🔥 ${program.streak}-day streak · ` : ""}${
      program.complete ? "You finished the whole sprint. 🎉" : `Day ${day + 1} unlocks tomorrow.`}</p>` : ""}
    ${milestone ? `<p class="milestone-hit">★ <b>${esc(milestone.when)}:</b> ${esc(milestone.title)}</p>` : ""}
    <ul>${lr.checks.map((k) => `<li><span class="${k.skipped ? "muted" : k.ok ? "ok" : "no"}">${k.skipped ? "–" : k.ok ? "✓" : "✗"}</span>
      <span>${esc(k.label)} <span class="muted">(you: ${esc(String(k.actual))})</span></span></li>`).join("")}</ul>
    <div class="row gap-s wrap-row">
      ${lr.passed && lesson?.next_lesson_id ? `<button class="primary sm" id="lrNext">Next lesson →</button>` : ""}
      ${!lr.passed ? `<button class="primary sm" id="lrRetry">🔁 Retry</button>` : ""}
      <button class="ghost sm" id="lrBack">Back to lesson</button>
    </div>`;
  $("lrBack").onclick = () => { showTab("learn"); openLesson(lr.lesson_id); };
  if ($("lrNext")) $("lrNext").onclick = () => { showTab("learn"); openLesson(lesson.next_lesson_id); };
  if ($("lrRetry")) $("lrRetry").onclick = startRetry;
}

async function loadLearn() {
  const [today, journeys, programs] = await Promise.all([api("/api/today"), api("/api/journey"), api("/api/programs")]);
  state.todayWords = today.words || [];
  renderPrograms(programs);
  renderToday(today);
  renderJourneys(journeys);
  if (!state.lesson || $("lessonView").classList.contains("hidden")) showLearnHome();
}
function showLearnHome() { $("learnHome").classList.remove("hidden"); $("lessonView").classList.add("hidden"); }

function renderToday(t) {
  $("todayTitle").textContent = t.completed === t.total ? "Session complete — nice work" : `About ${t.minutes} minutes today`;
  $("todayProgress").innerHTML = `<b>${t.completed}/${t.total}</b><br>done today · ${t.lessons_done}/${t.lessons_total} lessons passed`;
  $("todayItems").innerHTML = t.items.map((it, i) => `
    <div class="today-item ${it.done ? "done" : ""}">
      <div class="ti-h"><span class="ti-check">${it.done ? "✓" : i + 1}</span>${esc(it.title)}</div>
      <p>${esc(it.detail || "")}</p>
      ${it.prompt ? `<p><b>“${esc(it.prompt)}”</b></p>` : ""}
      <div class="ti-foot"><span class="small muted">${esc(it.mode)} · ~${it.minutes} min</span>
        <button class="${it.done ? "ghost" : "primary"} sm" data-today="${i}">${it.done ? "Again" : it.kind === "lesson" ? "Open lesson" : "Start"}</button></div>
    </div>`).join("");
  $("todayItems").querySelectorAll("[data-today]").forEach((b) => (b.onclick = () => {
    const it = t.items[+b.dataset.today];
    if (it.kind === "lesson") return openLesson(it.lesson_id);
    if (it.kind === "read_aloud") {
      return practiceWith({ mode: READ_ALOUD, prompt: it.prompt,
        ctx: { kind: "read_aloud", title: it.title, constraint: "Read at a natural pace — every word clear.", steps: [], target: [4, 15] } });
    }
    practiceWith({ mode: it.mode, prompt: it.prompt, challenge: it.challenge_mode,
      ctx: { kind: it.kind, title: it.title, constraint: it.constraint || (it.kind === "warmup" ? "No prep — start talking within 5 seconds." : ""),
        steps: it.steps || [], target: it.target_seconds || null } });
    if (it.kind === "warmup") $("status").textContent = "Hit “Start speaking” now — no prep.";
  }));
  $("todayWords").innerHTML = t.words?.length ? `
    <div class="tw-h"><span class="eyebrow">Today's words</span>
      <span class="small muted">Use them naturally in any answer today — the coach checks.</span></div>
    <div class="tw-list">${t.words.map((w) => `
      <div class="tw ${w.used ? "used" : ""}"><div class="row between"><b class="w" data-w="${esc(w.word)}">${esc(w.word)}</b>
        <span class="small">${w.used ? "✓ used" : esc(w.pos)}</span></div>
        <div class="small">${esc(w.definition)}</div><div class="small muted"><i>${esc(w.example)}</i></div></div>`).join("")}</div>` : "";
}

/* ------------------------------------------------------------------ 28-Day Speaking Sprint (v7) */
function renderPrograms(list) {
  const el = $("programCard");
  const p = list && list[0];
  if (!p) { el.innerHTML = ""; return; }
  const days = p.weeks.flatMap((w) => w.days);
  const cur = days.find((d) => d.day === p.current_day);
  const pct = (p.done / p.total) * 100;
  let cta;
  if (!p.enrolled) cta = `<button class="primary" data-prog-start>Start Day 1 →</button><span class="small">~10 min a day · 28 days</span>`;
  else if (p.complete) cta = `<b>🎉 Sprint complete — you speak like a leader.</b><button class="ghost sm" data-prog-restart>Start again</button>`;
  else if (p.today_status === "done") cta = `<b>✓ Done for today.</b><span class="small">Day ${p.current_day} — ${esc(cur.title)} — unlocks tomorrow.</span>`;
  else cta = `<button class="primary" data-day="${esc(cur.id)}">Start Day ${cur.day}: ${esc(cur.title)} →</button><span class="small">~${cur.minutes} min</span>`;
  el.innerHTML = `<section class="program ${p.enrolled ? "enrolled" : ""}" aria-label="${esc(p.title)}">
    <div class="pg-head">
      <div>
        <span class="pg-eyebrow">Daily sprint · 28 days</span>
        <h2 class="pg-title">${esc(p.title)}</h2>
        <p class="pg-tag">${esc(p.tagline)}</p>
      </div>
      ${p.enrolled ? `<div class="pg-stats"><div><b>${p.done}</b><span>of ${p.total} days</span></div>
        <div><b>${p.streak ? "🔥 " + p.streak : "–"}</b><span>day streak</span></div></div>` : ""}
    </div>
    <div class="pg-track" role="img" aria-label="${p.done} of ${p.total} days complete">
      <div class="pg-fill" style="width:${pct}%"></div>
      ${p.milestones.map((m) => `<span class="pg-mark ${m.reached ? "hit" : ""}" style="left:${(m.day / p.total) * 100}%"></span>`).join("")}
    </div>
    <div class="pg-milestones">${p.milestones.map((m) => `<div class="pg-ms ${m.reached ? "hit" : ""}">
      <span>${m.reached ? "★" : "☆"} ${esc(m.when)}</span><small>${esc(m.title)}</small></div>`).join("")}</div>
    <div class="pg-cta">${cta}</div>
    <div class="pg-weeks">${p.weeks.map((w) => `<div class="pg-week">
      <div class="pg-week-h">${esc(w.title)}</div>
      ${w.days.map((d) => `<button class="pg-day ${d.status}" data-day="${esc(d.id)}" title="${esc(d.summary)}">
        <span class="pg-dn">${d.status === "done" ? "✓" : d.day}</span>
        <span class="pg-dt">${esc(d.title)}</span>
        <span class="pg-ds">${d.status === "done" ? (d.best != null ? d.best : "") : d.status === "available" ? (p.enrolled || d.day === 1 ? "today" : "")
          : d.status === "tomorrow" ? "tomorrow" : "🔒"}</span>
      </button>`).join("")}</div>`).join("")}</div>
  </section>`;
  el.querySelectorAll("[data-day]").forEach((b) => (b.onclick = () => openLesson(b.dataset.day)));
  const start = el.querySelector("[data-prog-start]");
  if (start) start.onclick = async () => { await api(`/api/programs/${p.id}/start`, { method: "POST" }); openLesson(days[0].id); };
  const restart = el.querySelector("[data-prog-restart]");
  if (restart) restart.onclick = async () => {
    if (!confirm("Restart the Speaking Sprint from Day 1? Your past takes stay in History.")) return;
    await api(`/api/programs/${p.id}/start`, { method: "POST" }); loadLearn();
  };
}

function renderJourneys(list) {
  $("journeys").innerHTML = list.map((j, ji) => {
    let n = 0;
    return `<div class="card journey-card">
      <div class="row between wrap-row">
        <div><span class="eyebrow">Journey ${ji + 1}</span>
          <div class="card-h" style="margin:2px 0 4px">${esc(j.title)}</div>
          <p class="muted small" style="margin:0">${esc(j.description)}</p></div>
        <div class="journey-meter"><div class="jm-bar"><div class="jm-fill" style="width:${(j.done / Math.max(1, j.total)) * 100}%"></div></div>
          <span class="small">${j.done}/${j.total} passed</span></div>
      </div>
      ${j.modules.map((m) => `
        <div class="module"><div class="module-h"><b>${esc(m.title)}</b><span class="small muted">${esc(m.goal)}</span></div>
        ${m.lessons.map((l) => { n += 1; return `
          <button class="lesson-row ${l.status}" data-lesson="${esc(l.id)}">
            <span class="lr-icon">${l.status === "done" ? "✓" : n}</span>
            <span><span class="lr-title">${esc(l.title)}</span><br><span class="lr-sub">${esc(l.framework)} · ${l.kind === "read_aloud" ? "🗣️ Read aloud" : esc(l.mode)} · ${l.minutes} min</span></span>
            <span class="lr-meta">${l.status === "current" ? "<b>Up next</b>" : l.best != null ? `best ${l.best}` : ""}${l.attempts ? `<br>${l.attempts} take${l.attempts > 1 ? "s" : ""}` : ""}</span>
          </button>`; }).join("")}</div>`).join("")}
    </div>`;
  }).join("");
  $("journeys").querySelectorAll("[data-lesson]").forEach((b) => (b.onclick = () => openLesson(b.dataset.lesson)));
}

async function openLesson(id) {
  const l = await api(`/api/lessons/${encodeURIComponent(id)}`);
  state.lesson = l;
  $("learnHome").classList.add("hidden"); $("lessonView").classList.remove("hidden");
  $("lessonEyebrow").textContent = l.program
    ? `Day ${l.program.day} of ${l.count} · ${l.module_title} · ~${l.minutes} min`
    : `Lesson ${l.index} of ${l.count} · ${l.module_title} · ~${l.minutes} min`;
  const lessonLabel = l.program ? `Day ${l.program.day}: ${l.title}` : `Lesson ${l.index}: ${l.title}`;
  const gate = l.program?.gate;
  $("lessonGate").textContent = gate ? `🔒 ${gate} You can read the lesson now.` : "";
  $("lessonGate").classList.toggle("hidden", !gate);
  $("practiceLessonBtn").disabled = !!gate;
  $("lessonTitle").textContent = l.title;
  $("lessonSummary").textContent = l.summary || "";
  $("lessonConcept").innerHTML = l.concept.map((p) => `<p>${esc(p)}</p>`).join("");
  $("fwName").textContent = l.framework.name;
  $("fwSteps").innerHTML = l.framework.steps.map((s) => `<li><div><b>${esc(s.label)}</b><span>${esc(s.desc)}</span></div></li>`).join("");
  $("cheatSheet").innerHTML = (l.cheat_sheet || []).map((c) => `<li>${esc(c)}</li>`).join("");
  const ex = l.example || {};
  $("exPrompt").textContent = ex.prompt ? `Prompt: ${ex.prompt}` : "";
  $("exBefore").textContent = ex.before || ""; $("exAfter").textContent = ex.after || ""; $("exWhy").textContent = ex.why || "";
  const e = l.exercise;
  $("exInstructions").textContent = e.instructions;
  $("exConstraint").textContent = e.constraint || "—";
  const pass = e.pass || {};
  $("exPass").innerHTML = [`Overall ≥ ${pass.overall ?? 60}`, ...(pass.checks || []).map((c) => c.label + (c.audio ? " 🎤" : ""))].map((x) => `<li>${esc(x)}</li>`).join("");
  const ra = l.read_aloud;
  const prompts = ra ? ra.items.map((i) => i.text) : e.prompts;
  $("exPromptSelect").previousElementSibling.textContent = ra ? "Sentence to read" : "Prompt";
  $("exPromptSelect").innerHTML = prompts.map((p) => `<option>${esc(p)}</option>`).join("");
  const pr = l.progress || {};
  $("lessonProgressNote").textContent = pr.passed ? `✓ Passed · best ${pr.best}` : pr.attempts ? `${pr.attempts} take(s) · best ${pr.best}` : "";
  $("practiceLessonBtn").onclick = () => ra ? practiceWith({
    mode: READ_ALOUD, prompt: $("exPromptSelect").value,
    ctx: { kind: "lesson", lessonId: l.id, title: lessonLabel, constraint: e.constraint,
      steps: [e.instructions], target: e.target_seconds || [4, 12], prompts,
      focus: ra.items.flatMap((i) => i.focus || []) },
  }) : practiceWith({
    mode: e.mode, prompt: $("exPromptSelect").value, challenge: !!e.challenge_mode,
    ctx: { kind: "lesson", lessonId: l.id, title: lessonLabel, constraint: e.constraint,
      steps: [e.instructions], framework: l.framework.steps.map((s) => s.label), frameworkName: l.framework.name,
      target: e.target_seconds, prompts: e.prompts },
  });
  window.scrollTo({ top: 0 });
}
$("backToJourney").onclick = () => { state.lesson = null; showLearnHome(); loadLearn(); };

/* ------------------------------------------------------------------ pronunciation & language (v5) */
const lookupCache = {};
async function lookup(words) {
  const need = words.filter((w) => !(w in lookupCache));
  if (need.length) (await api(`/api/pronounce/lookup?words=${encodeURIComponent(need.join(","))}`))
    .forEach((d, i) => (lookupCache[need[i]] = d));
  return words.map((w) => lookupCache[w]);
}
const normWord = (w) => w.toLowerCase().replace(/[’]/g, "'").replace(/[^a-z0-9']/g, "").replace(/^'+|'+$/g, "");

function wordsHtml(text, focus = [], statusByIndex = null) {
  const f = new Set(focus.map(normWord));
  let i = 0;
  return esc(text).replace(/[A-Za-z][A-Za-z'’]*/g, (w) => {
    const n = normWord(w);
    const st = statusByIndex ? statusByIndex[i] : null; i++;
    return `<span class="w ${f.has(n) ? "focus" : ""} ${st ? "st-" + st : ""}" data-w="${esc(n)}" tabindex="0">${w}</span>`;
  });
}

let voiceCache = null;
function speak(text, rate = 0.9) {
  if (!("speechSynthesis" in window)) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = "en-US"; u.rate = rate;
  voiceCache = voiceCache || speechSynthesis.getVoices().find((v) => /en[-_]US/i.test(v.lang));
  if (voiceCache) u.voice = voiceCache;
  speechSynthesis.speak(u);
}

async function showWordPop(word, anchor) {
  const pop = $("wordPop");
  const [d] = await lookup([word]);
  pop.innerHTML = d && d.known ? `
      <div class="row between"><b>${esc(d.word)}</b><button class="ghost sm" id="popClose" aria-label="Close">✕</button></div>
      <div class="respell">${esc(d.respell)}</div>
      <div class="small muted">${d.stress ? `Stress on the ${esc(d.stress)}` : `${d.syllables} syllable`}${d.sounds.length ? ` · sounds: ${d.sounds.map(esc).join(", ")}` : ""}</div>
      <div class="row gap-s" style="margin-top:8px"><button class="primary sm" id="popHear">▶ Hear</button>
        <button class="ghost sm" id="popSlow">🐢 Slowly</button></div>`
    : `<div class="row between"><b>${esc(word)}</b><button class="ghost sm" id="popClose">✕</button></div>
       <div class="small muted">Not in the dictionary.</div>
       <div class="row gap-s" style="margin-top:8px"><button class="primary sm" id="popHear">▶ Hear</button></div>`;
  pop.classList.remove("hidden");
  const r = anchor.getBoundingClientRect();
  pop.style.left = `${Math.min(r.left, innerWidth - pop.offsetWidth - 12)}px`;
  pop.style.top = `${r.bottom + 6 + scrollY}px`;
  $("popClose").onclick = () => pop.classList.add("hidden");
  $("popHear").onclick = () => speak(word, 0.85);
  if ($("popSlow")) $("popSlow").onclick = () => speak(word, 0.55);
}
document.addEventListener("click", (e) => {
  const w = e.target.closest(".w[data-w]");
  if (w) { e.preventDefault(); showWordPop(w.dataset.w, w); return; }
  if (!e.target.closest("#wordPop")) $("wordPop").classList.add("hidden");
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && e.target.matches?.(".w[data-w]")) showWordPop(e.target.dataset.w, e.target);
  if (e.key === "Escape") $("wordPop").classList.add("hidden");
});

function renderPronunciation(a) {
  const el = $("pronCard");
  if (a.kind !== "read_aloud") { el.classList.add("hidden"); return; }
  const p = a.pronunciation;
  const statuses = p.words.map((w) => w.status);
  const problems = p.words.filter((w) => w.status !== "good");
  el.classList.remove("hidden");
  el.innerHTML = `
    <h3 style="margin-top:0">🗣️ Word by word</h3>
    <p class="ra-target">${wordsHtml(p.target, [], statuses)}</p>
    <div class="wave-legend small muted"><span><i class="lg st-good"></i>clear</span><span><i class="lg st-unclear"></i>unclear</span>
      <span><i class="lg st-substituted"></i>heard as another word</span><span><i class="lg st-missed"></i>missed</span></div>
    ${problems.length ? `<table class="ptable pron-table"><tr><th>Word</th><th>What happened</th><th>Say it like</th><th></th></tr>
      ${problems.map((w) => `<tr><td><b>${esc(w.word)}</b></td>
        <td>${w.status === "substituted" ? `heard “${esc(w.heard)}”${(w.confusions || []).length ? ` <span class="tag">${w.confusions.map((c) => esc(c.label)).join(", ")}</span>` : ""}`
          : w.status === "missed" ? "didn't come through" : `unclear (${Math.round((w.confidence || 0) * 100)}% confidence)`}</td>
        <td>${w.guide?.known ? `<span class="respell-sm">${esc(w.guide.respell)}</span>` : "—"}</td>
        <td><button class="ghost sm" data-say="${esc(w.word)}">▶</button></td></tr>`).join("")}</table>`
      : `<p class="small">Every word came through clearly. 🎉</p>`}
    ${p.extra_words.length ? `<p class="small muted">Extra words heard: ${p.extra_words.map(esc).join(", ")}</p>` : ""}
    <p class="small muted">Estimated from which words the speech recognizer heard — a word-level check, not a phoneme-level measurement. Sound labels (e.g. “th → s”) are the likely swap based on the dictionary spelling of the two words. ${p.has_confidence ? "" : "Turn on local Whisper to also flag unclear words."}</p>`;
  el.querySelectorAll("[data-say]").forEach((b) => (b.onclick = () => speak(b.dataset.say, 0.8)));
}

function scoreBar(label, v) {
  return `<div class="dim"><span>${esc(label)}</span><div class="dim-bar"><div style="width:${v ?? 0}%"></div></div><b>${v ?? "—"}</b></div>`;
}

function renderLanguage(a) {
  const el = $("languageCard"), L = a.language;
  if (!L || a.kind === "read_aloud") { el.classList.add("hidden"); return; }
  const v = L.vocabulary, sn = L.sentences, ch = a.challenge_mode;
  const chips = (xs, cls) => xs.map((x) => `<span class="chip ${cls}">${x}</span>`).join("");
  const lens = sn.lengths || [];
  const maxLen = Math.max(30, ...lens);
  el.classList.remove("hidden");
  el.innerHTML = `
    <h3 style="margin-top:0">📝 Words & sentences</h3>
    <div class="lang-grid">
      <div>
        ${scoreBar("Vocabulary", v.score)}
        <p class="small muted">Variety ${Math.round(v.diversity * 100)}% · ${Math.round(v.advanced_share * 100)}% precise words · ${v.content_words} content words</p>
        ${v.advanced_words.length ? `<div class="eyebrow">Precise words you used</div><div class="chips">${chips(v.advanced_words.map((w) => `<span class="w" data-w="${esc(w)}">${esc(w)}</span>`), "good")}</div>` : ""}
        ${v.weak.length ? `<div class="eyebrow">Vague or weak wording</div><div class="chips">${chips(v.weak.map((w) =>
          `“${esc(w.phrase)}” ×${w.count}${!ch && w.alternatives.length ? ` → ${w.alternatives.slice(0, 2).map(esc).join(" / ")}` : ""}`), "warn")}</div>` : ""}
        ${v.overused.length ? `<div class="eyebrow">Repeated</div><div class="chips">${chips(v.overused.map((o) => `${esc(o.word)} ×${o.count}`), "")}</div>` : ""}
        ${L.word_upgrades?.length ? `<div class="eyebrow">AI word upgrades</div><ul class="small">${L.word_upgrades.map((u) =>
          `<li>“${esc(u.original)}”${u.better ? ` → <b>${esc(u.better)}</b>` : ""} <span class="muted">${esc(u.why)}</span></li>`).join("")}</ul>` : ""}
        ${state.todayWords?.length ? `<div class="eyebrow">Today's words</div><div class="chips">${chips(state.todayWords.map((w) =>
          (v.daily_used || []).includes(w.word) ? `✓ ${esc(w.word)}` : esc(w.word)), "")}</div>` : ""}
      </div>
      <div>
        ${scoreBar("Sentence structure", sn.score)}
        ${sn.method === "none" ? `<p class="small muted">${esc(sn.note)}</p>` : `
          <p class="small muted">${sn.count} sentences · average ${sn.avg_length} words · longest ${sn.longest}${sn.method === "pauses" ? " · split by your pauses" : ""}</p>
          <div class="sent-bars" role="img" aria-label="Sentence lengths">${lens.map((n, i) =>
            `<div class="sb ${n > 30 ? "long" : ""}" style="height:${Math.max(6, (n / maxLen) * 100)}%" title="Sentence ${i + 1}: ${n} words"></div>`).join("")}</div>
          <p class="small muted" style="margin-top:2px">Each bar is one sentence · aim for 10–22 words, with some variety</p>`}
        ${(sn.run_ons || []).length ? `<div class="eyebrow">Run-on</div><p class="quote small">“${esc(sn.run_ons[0].slice(0, 160))}${sn.run_ons[0].length > 160 ? "…" : ""}”</p>` : ""}
        ${sn.repeated_opener ? `<p class="small">🔁 Most sentences start with “<b>${esc(sn.repeated_opener)}</b>”.</p>` : ""}
        ${(sn.passive || []).length ? `<p class="small">Passive: ${sn.passive.map((x) => `“${esc(x)}”`).join(", ")}</p>` : ""}
        ${(sn.grammar || []).length ? `<div class="eyebrow">Grammar</div><ul class="small">${sn.grammar.map((g) =>
          `<li>“${esc(g.original)}”${g.suggestion ? ` → <b>${esc(g.suggestion)}</b>` : ""} <span class="muted">${esc(g.issue)}</span></li>`).join("")}</ul>` : ""}
        ${L.sentence_tip ? `<p class="small">💡 ${esc(L.sentence_tip)}</p>` : ""}
      </div>
    </div>
    ${(L.unclear_words || []).length ? `<div class="eyebrow" style="margin-top:10px">Words that came out unclear</div>
      <div class="chips">${L.unclear_words.map((u) => `<span class="chip warn"><span class="w" data-w="${esc(u.word)}">${esc(u.word)}</span> · ${Math.round(u.confidence * 100)}%</span>`).join("")}</div>` : ""}`;
}

/* ------------------------------------------------------------------ pronounce tab */
async function loadPronounce() {
  if (!state.sets) state.sets = await api("/api/pronounce/sets");
  const prog = await api("/api/progress");
  state.langProg = prog.language || {};
  $("setChips").innerHTML = state.sets.map((st) => `<button class="chip ${st.id === (state.setId || state.sets[0].id) ? "active" : ""}" data-set="${esc(st.id)}">${esc(st.title)}</button>`).join("");
  $("setChips").querySelectorAll("[data-set]").forEach((b) => (b.onclick = () => { state.setId = b.dataset.set; loadPronounce(); }));
  renderSet(state.sets.find((x) => x.id === state.setId) || state.sets[0]);
  renderSoundSummary(state.langProg);
}

function renderSet(st) {
  $("setCard").innerHTML = `
    <span class="eyebrow">${esc(st.kind === "sounds" ? "Sound pair" : st.kind === "stress" ? "Word stress" : "Clarity")}</span>
    <div class="card-h" style="margin:2px 0 6px">${esc(st.title)}</div>
    <p class="small">${esc(st.description)}</p>
    ${st.tip ? `<p class="small tip">💡 ${esc(st.tip)}</p>` : ""}
    <div class="ra-items">${st.items.map((it, i) => `
      <div class="ra-item">
        <p class="ra-text">${wordsHtml(it.text, it.focus || [])}</p>
        ${it.tip ? `<p class="small muted">${esc(it.tip)}</p>` : ""}
        <div class="row gap-s wrap-row"><button class="ghost sm" data-hear="${i}">▶ Hear</button>
          <button class="ghost sm" data-slow="${i}">🐢 Slowly</button>
          <button class="primary sm" data-practice="${i}">🎙️ Record this</button></div>
      </div>`).join("")}</div>`;
  $("setCard").querySelectorAll("[data-hear]").forEach((b) => (b.onclick = () => speak(st.items[+b.dataset.hear].text, 0.9)));
  $("setCard").querySelectorAll("[data-slow]").forEach((b) => (b.onclick = () => speak(st.items[+b.dataset.slow].text, 0.6)));
  $("setCard").querySelectorAll("[data-practice]").forEach((b) => (b.onclick = () => {
    const it = st.items[+b.dataset.practice];
    practiceWith({ mode: READ_ALOUD, prompt: it.text, ctx: { kind: "read_aloud", from: "pronounce", title: st.title,
      constraint: st.tip || "Read at a natural pace.", steps: [], focus: it.focus || [], prompts: st.items.map((x) => x.text), target: [4, 15] } });
  }));

  const pairs = st.pairs_info || [];
  const focus = [...new Set(st.items.flatMap((i) => i.focus_info || []).filter((d) => d.known).map((d) => JSON.stringify(d)))].map((x) => JSON.parse(x));
  $("pairsCard").innerHTML = pairs.length ? `
    <div class="card-h">Minimal pairs</div>
    <p class="small muted">Only one sound changes. Hear both, then say both — the difference should be obvious.</p>
    <div class="pairs">${pairs.map(([a, b]) => `
      <div class="pair"><button class="ghost sm" data-say="${esc(a.word)}">▶ ${esc(a.word)}</button><span class="muted">vs</span>
        <button class="ghost sm" data-say="${esc(b.word)}">▶ ${esc(b.word)}</button>
        <span class="respell-sm">${esc(a.respell || "")} · ${esc(b.respell || "")}</span></div>`).join("")}</div>`
    : `<div class="card-h">Stress guide</div>
       <p class="small muted">CAPITALS = the stressed syllable. Make it longer, louder and higher.</p>
       <table class="ptable">${focus.slice(0, 12).map((d) => `<tr><td><b class="w" data-w="${esc(d.word)}">${esc(d.word)}</b></td>
         <td class="respell-sm">${esc(d.respell)}</td><td class="small muted">${esc(d.stress || "")}</td>
         <td><button class="ghost sm" data-say="${esc(d.word)}">▶</button></td></tr>`).join("")}</table>`;
  $("pairsCard").querySelectorAll("[data-say]").forEach((b) => (b.onclick = () => speak(b.dataset.say, 0.8)));

  const lp = state.langProg || {};
  const trouble = lp.trouble_words || [];
  $("troubleCard").innerHTML = `<div class="card-h">Your trouble words</div>` + (trouble.length
    ? `<p class="small muted">Words that were missed, swapped or unclear in recent drills. Tap to practise.</p>
       <div class="chips">${trouble.map(([w, n]) => `<span class="chip warn"><span class="w" data-w="${esc(w)}">${esc(w)}</span> ×${n}</span>`).join("")}</div>`
    : `<p class="small muted">Record a few sentences — words that don't come through will collect here.</p>`);
}

function renderSoundSummary(lp) {
  const sounds = Object.entries(lp.sounds || {});
  $("soundSummary").innerHTML = sounds.length ? `<div class="eyebrow">Sound groups — words recognised (estimate)</div>` + sounds.sort((a, b) => a[1].accuracy - b[1].accuracy)
    .map(([g, v]) => `<div class="fbar"><span>${esc(g)}</span><div class="t"><div style="width:${v.accuracy}%;${v.accuracy < 85 ? "background:var(--bad)" : ""}"></div></div><b>${v.accuracy}%</b></div>`).join("")
    : `<span class="small muted">Sound-group results appear after a few drills.</span>`;
}

function renderLangProgress(lp) {
  if (!lp) { $("langProgress").innerHTML = ""; return; }
  const k = [[lp.vocabulary_avg, "Vocabulary (last 10)"], [lp.sentences_avg, "Sentence structure (last 10)"],
    [lp.pronunciation_avg, "Words recognised in drills (last 10, %)"], [(lp.words_used || []).length, "Daily words you've used"]];
  $("langProgress").innerHTML = `
    <div class="card">
      <div class="card-h">🗣️ Language skills</div>
      <div class="kpis" style="margin-bottom:12px">${k.map(([v, l]) => `<div class="kpi"><div class="v">${v ?? "—"}</div><div class="l">${l}</div></div>`).join("")}</div>
      <div class="grid2" style="margin:0">
        <div><div class="eyebrow">Sounds</div><div id="lpSounds"></div></div>
        <div>
          ${lp.top_weak_words?.length ? `<div class="eyebrow">Your most common vague words</div><div class="chips">${lp.top_weak_words.map(([w, n]) => `<span class="chip warn">${esc(w)} ×${n}</span>`).join("")}</div>` : ""}
          ${lp.trouble_words?.length ? `<div class="eyebrow" style="margin-top:10px">Pronunciation trouble words</div><div class="chips">${lp.trouble_words.map(([w, n]) => `<span class="chip"><span class="w" data-w="${esc(w)}">${esc(w)}</span> ×${n}</span>`).join("")}</div>` : ""}
          ${lp.words_used?.length ? `<div class="eyebrow" style="margin-top:10px">Words added to your vocabulary</div><div class="chips">${lp.words_used.map(([w, n]) => `<span class="chip good">${esc(w)} ×${n}</span>`).join("")}</div>` : ""}
        </div>
      </div>
    </div>`;
  const sounds = Object.entries(lp.sounds || {});
  $("lpSounds").innerHTML = sounds.length ? sounds.map(([g, v]) => `<div class="fbar"><span>${esc(g)}</span><div class="t"><div style="width:${v.accuracy}%"></div></div><b>${v.accuracy}%</b></div>`).join("")
    : `<p class="small muted">Do a few drills in the Pronounce tab.</p>`;
}

/* ------------------------------------------------------------------ accounts */
let authView = "signup";
function showAuth(view = "signup", message = "") {
  authView = view;
  const up = view === "signup";
  document.body.classList.add("signed-out");
  $("authScreen").classList.remove("hidden");
  $("authTitle").textContent = up ? "Create your account" : "Welcome back";
  $("authSub").textContent = up ? "Practice out loud, get coached on content and voice, and track your progress."
    : "Sign in to pick up where you left off.";
  $("nameField").classList.toggle("hidden", !up);
  $("pwHint").classList.toggle("hidden", !up);
  $("authPassword").autocomplete = up ? "new-password" : "current-password";
  $("authSubmit").textContent = up ? "Create account" : "Sign in";
  $("authSwitchText").textContent = up ? "Already have an account?" : "New here?";
  $("authSwitch").textContent = up ? "Sign in" : "Create an account";
  $("authError").textContent = message;
  $("authError").classList.toggle("hidden", !message);
  setTimeout(() => $(up ? "authName" : "authEmail").focus(), 0);
}
$("authSwitch").onclick = (e) => { e.preventDefault(); showAuth(authView === "signup" ? "signin" : "signup"); };
$("authForm").onsubmit = async (e) => {
  e.preventDefault();
  const btn = $("authSubmit"); btn.disabled = true;
  const body = { email: $("authEmail").value, password: $("authPassword").value };
  if (authView === "signup") body.name = $("authName").value;
  try {
    const r = await api(`/api/auth/${authView === "signup" ? "signup" : "login"}`, { method: "POST", body: JSON.stringify(body) });
    $("authPassword").value = "";
    location.reload();          // start the app fresh as this user
    return r;
  } catch (err) {
    $("authError").textContent = err.message; $("authError").classList.remove("hidden");
  } finally { btn.disabled = false; }
};

function showAccount(me) {
  if (me.mode !== "accounts" || !me.user) return;
  const u = me.user, label = (u.name || u.email).trim();
  $("accountMenu").classList.remove("hidden");
  $("dangerZone").classList.remove("hidden");
  $("accountBtn").textContent = label.slice(0, 1).toUpperCase();
  $("accountBtn").setAttribute("aria-label", `Account: ${u.email}`);
  $("accountName").textContent = u.name || "Signed in";
  $("accountEmail").textContent = u.email;
}
$("accountBtn").onclick = (e) => {
  e.stopPropagation();
  const open = $("accountPop").classList.toggle("hidden") === false;
  $("accountBtn").setAttribute("aria-expanded", open);
};
document.addEventListener("click", (e) => { if (!$("accountMenu").contains(e.target)) $("accountPop").classList.add("hidden"); });
$("signOutBtn").onclick = async () => { await api("/api/auth/logout", { method: "POST" }).catch(() => {}); location.reload(); };
$("deleteAccountBtn").onclick = async () => {
  const err = $("deleteError");
  if (!$("deletePw").value) { err.textContent = "Enter your password to confirm."; err.classList.remove("hidden"); return; }
  if (!confirm("Delete your account and all your practice permanently?")) return;
  try {
    await api("/api/auth/account", { method: "DELETE", body: JSON.stringify({ password: $("deletePw").value }) });
    location.reload();
  } catch (e) { err.textContent = e.message; err.classList.remove("hidden"); }
};

async function boot() {
  const me = await api("/api/auth/me");
  if (me.mode === "accounts" && !me.user) return showAuth("signup");
  showAccount(me);
  await init();
  resumeActiveJob();
}
boot().catch((e) => { $("status").textContent = `Failed to load: ${e.message}`; });
