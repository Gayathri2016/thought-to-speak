/* Thought → Speak — main.js: Entry point: theme, tabs and start-up. Loaded with <script type="module">. */
import { $, esc, state } from "./core.js";
import { api } from "./api.js";
import { boot } from "./auth.js";
import { loadLearn, loadPronounce } from "./curriculum.js";
import { loadHistory, loadProgress } from "./progress.js";
import { clearCtx, setMode, setupSpeech } from "./recording.js";
import { dayStatus, justReachedGoal, takesToday } from "./habit.js";

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
export function showTab(name) {
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tabpanel").forEach((p) => p.classList.toggle("hidden", p.id !== `tab-${name}`));
  if (name === "learn") loadLearn();
  if (name === "pronounce") loadPronounce();
  if (name === "progress") loadProgress();
  if (name === "history") loadHistory();
  $("dayStrip").dataset.tab = name;
  window.scrollTo({ top: 0 });
}

/* ------------------------------------------------------------------ the day strip: goal, streak, next step */
let lastToday = null;
export async function refreshDay({ celebrate = false } = {}) {
  let hist, prog;
  try {
    [hist, prog] = await Promise.all([api("/api/history?limit=60"), api("/api/progress")]);
  } catch (_) { return; }                                // never block practice on the strip
  const today = takesToday(hist);
  const st = dayStatus({ today, streak: prog.streak_days || 0, total: prog.total_attempts || hist.length });
  const R = 15, C = 2 * Math.PI * R, seg = C / st.goal, gap = 3;
  const arcs = Array.from({ length: st.goal }, (_, i) =>
    `<circle cx="20" cy="20" r="${R}" class="${i < st.today ? "on" : ""}" stroke-dasharray="${seg - gap} ${C - seg + gap}"
      stroke-dashoffset="${-i * seg}"/>`).join("");
  const name = state.userName ? `Hi ${esc(state.userName)}` : "Today";
  $("dayStrip").innerHTML = `
    <div class="ds-goal ${st.done ? "done" : ""}" role="img" aria-label="${st.today} of ${st.goal} takes today">
      <svg viewBox="0 0 40 40">${arcs}</svg><span>${st.done ? "✓" : `${st.today}/${st.goal}`}</span></div>
    <div class="ds-text"><b>${name}</b><span>${esc(st.line)}</span></div>
    ${st.streak ? `<div class="ds-streak" title="Days in a row with at least one take"><span aria-hidden="true">🔥</span>${st.streakLabel}</div>` : ""}
    <button class="primary sm ds-cta" id="dsCta">Practice now</button>`;
  $("dsCta").onclick = () => showTab("practice");
  $("dayStrip").classList.remove("hidden");
  if (celebrate && lastToday !== null && justReachedGoal(lastToday, today, st.goal)) {
    toast(`Daily goal reached — ${st.goal} takes today${st.streak > 1 ? `, ${st.streakLabel} in a row` : ""}. Nice work.`);
  }
  lastToday = today;
}

export function toast(msg) {
  const t = $("toast");
  t.textContent = msg; t.classList.remove("hidden", "out");
  clearTimeout(t._h);
  t._h = setTimeout(() => { t.classList.add("out"); t._h = setTimeout(() => t.classList.add("hidden"), 400); }, 4200);
}

/* ------------------------------------------------------------------ setup */
export async function init() {
  state.config = await api("/api/config");
  state.config.modes.forEach((m) => (state.modes[m.name] = m));
  const badge = $("providerBadge");
  const beta = state.config.beta || {};
  if (beta.on) {
    $("betaBadge").classList.remove("hidden");
    $("footBeta").classList.remove("hidden");
  }
  if (beta.feedback_url) {
    $("footFeedback").href = beta.feedback_url;
    $("footFeedback").classList.remove("hidden");
  }
  const stt = { openai: "Whisper API", local: "local Whisper", none: "your browser" }[state.config.transcriber] || state.config.transcriber;
  if (state.config.provider === "local") {
    badge.textContent = "Built-in coach";
    badge.classList.add("local");
    badge.title = `Coaching runs on this server, no AI service needed. Transcript: ${stt}. ` +
      "Add OPENAI_API_KEY or ANTHROPIC_API_KEY in .env for AI coaching.";
  } else {
    badge.textContent = "AI coach";
    badge.title = `AI: ${state.config.provider} · ${state.config.model}. Transcript: ${stt}.`;
  }
  const chips = (modes) => modes.map((m) =>
    `<button class="chip" role="radio" aria-checked="false" data-mode="${esc(m.name)}">${esc(m.name)}</button>`).join("");
  const selectableModes = state.config.modes.filter((m) => m.selectable !== false);
  $("modeChips").innerHTML = `<div class="chips">${chips(selectableModes.filter((m) => m.group === "Everyday life"))}</div>
    <details><summary>Work &amp; presentations</summary><div class="chips">${chips(selectableModes.filter((m) => m.group !== "Everyday life"))}</div></details>`;
  $("modeChips").querySelectorAll(".chip").forEach((c) => (c.onclick = () => { clearCtx(); setMode(c.dataset.mode); }));
  $("historyMode").innerHTML += selectableModes.map((m) => `<option>${esc(m.name)}</option>`).join("");
  let saved = null;
  try { saved = localStorage.getItem("tts-mode"); } catch (_) {}
  setMode(state.modes[saved] && state.modes[saved].selectable !== false ? saved : "Everyday Conversation");
  $("transcriptNote").textContent = state.config.transcriber === "none"
    ? "Live transcript is a draft: the browser may change words or leave out um/uh. Review it before analyzing; filler counts cover only the words captured."
    : "Live transcript is a draft. After you stop, we transcribe the recording for coaching. Recognition can still miss words and fillers; compare it with your recording.";
  setupSpeech();
  showTab("practice");
  refreshDay();
}
boot().catch((e) => { $("status").textContent = `Failed to load: ${e.message}`; });
