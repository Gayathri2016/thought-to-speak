/* Thought → Speak — main.js: Entry point: theme, tabs and start-up. Loaded with <script type="module">. */
import { $, esc, state } from "./core.js";
import { api } from "./api.js";
import { boot } from "./auth.js";
import { loadLearn, loadPronounce } from "./curriculum.js";
import { loadHistory, loadProgress } from "./progress.js";
import { clearCtx, setMode, setupSpeech } from "./recording.js";

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
  window.scrollTo({ top: 0 });
}

/* ------------------------------------------------------------------ setup */
export async function init() {
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
boot().catch((e) => { $("status").textContent = `Failed to load: ${e.message}`; });
