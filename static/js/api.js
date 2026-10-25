/* Thought → Speak — api.js: The HTTP client and the analysis-job API (submit, poll, remember the active job). */
import { $, TZ, state } from "./core.js";
import { showAuth } from "./auth.js";
import { encodeWav } from "./recording.js";

export async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json", "X-Timezone": TZ }, credentials: "same-origin", ...opts });
  const data = await r.json().catch(() => ({}));
  if (r.status === 401 && !path.startsWith("/api/auth/")) showAuth("signin", "Your session ended — please sign in again.");
  if (!r.ok) throw new Error(data.detail || `Request failed (${r.status})`);
  return data;
}

/* ------------------------------------------------------------------ analysis jobs (v8.4) */
export async function submitJob(body, hasAudio, key) {
  const headers = { "X-Timezone": TZ, "Idempotency-Key": key };
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

export function rememberJob(id) { try { id ? localStorage.setItem("tts-active-job", id) : localStorage.removeItem("tts-active-job"); } catch (_) {} }

export async function pollJob(id) {
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
