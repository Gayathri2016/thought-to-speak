/* Thought → Speak — recording.js: Practice: prompts and modes, the microphone and live transcript, recording, submitting takes. */
import { $, esc, fmtTime, state } from "./core.js";
import { api, pollJob, rememberJob, submitJob } from "./api.js";
import { openLesson, renderLessonResult } from "./curriculum.js";
import { showTab } from "./main.js";
import { renderAnalysis, renderComparison, renderFocus, renderLanguage, renderPronunciation, renderVoice, speak, syncMore, wordsHtml } from "./results.js";

export const READ_ALOUD = "Read aloud";
export function setMode(mode, prompt) {
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
export function practiceWith({ mode, prompt, ctx = null, challenge = false }) {
  state.ctx = ctx;
  showTab("practice");
  setMode(mode, prompt);
  $("challenge").checked = !!challenge;
  renderCtxBanner();
}
export function clearCtx() { state.ctx = null; $("recBanner").classList.add("hidden"); }

export function setReadAloud(text) {
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
export function renderCtxBanner() {
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

export function setPrompt(p) {
  state.prompt = p; state.customPrompt = false;
  $("promptText").textContent = p; $("promptText").classList.remove("hidden");
  $("promptEdit").classList.add("hidden");
  $("customPromptBtn").textContent = "✎ Custom";
  newChain();
}

export function newChain() {
  state.chainId = null; state.takeNo = 1; state.lastAttempt = null;
  $("takeLabel").textContent = "Take 1";
  $("previousTakeCard").classList.add("hidden");
  $("compareCard").classList.add("hidden");
  resetTake();
}

export function resetTake() {
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

export function startRetry() {
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
export function setupSpeech() {
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

export async function startRecording() {
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

export function stopRecording() {
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

export function currentSeconds() {
  return state.elapsed + (state.recording ? (performance.now() - state.tickStart) / 1000 : 0);
}
export function renderTimer() {
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
export async function analyze() {
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

export function showJobResult(job) {
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
export async function resumeActiveJob() {
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
export function setPromptKeepChain(p) {
  state.prompt = p; state.customPrompt = false;
  $("promptText").textContent = p; $("promptText").classList.remove("hidden"); $("promptEdit").classList.add("hidden");
  $("customPromptBtn").textContent = "✎ Custom";
}

/* ------------------------------------------------------------------ real audio capture (v4) */
export const WORKLET_SRC = `class TTSRec extends AudioWorkletProcessor {
  process(inputs) { const ch = inputs[0] && inputs[0][0]; if (ch) this.port.postMessage(ch.slice(0)); return true; }
}
registerProcessor("tts-rec", TTSRec);`;

export async function startCapture() {
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

export function stopCapture() {
  const c = state.cap; if (!c) return;
  try { c.src.disconnect(); c.node.disconnect(); c.mute.disconnect(); } catch (_) {}
  c.stream.getTracks().forEach((t) => t.stop());
  c.ctx.close();
  state.cap = null;
  $("micLabel").textContent = "🎤 Mic off"; $("levelFill").style.width = "0";
}

export function audioSeconds() {
  let n = 0; for (const c of state.audioChunks) n += c.length;
  return n / (state.audioRate || 48000);
}

export function encodeWav(chunks, rate, outRate = 16000) {
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
