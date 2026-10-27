/* Thought → Speak — core.js: Dependency-free helpers and shared state. Every module imports this first, so it is always initialised first. */

/* Thought → Speak v2 — front-end (no build step, no external deps) */
export const $ = (id) => document.getElementById(id);
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const fmtTime = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
export const DIMS = ["structure", "clarity", "relevance", "answer_quality", "delivery"];

export const state = {
  config: null, modes: {}, mode: "Everyday Conversation", prompt: "", customPrompt: false,
  ctx: null, target: [60, 120], journey: null, lesson: null,
  chainId: null, takeNo: 1, lastAttempt: null, rec: null,
  recording: false, audioChunks: [], audioRate: 48000, cap: null, peakLevel: 0, noSR: false,
  elapsed: 0, tickStart: null, timerId: null, spokeSeconds: 0,
};

export const TZ = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (_) { return ""; } })();
