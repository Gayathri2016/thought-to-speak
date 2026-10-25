/* Thought → Speak — progress.js: Progress dashboard, charts and take history. */
import { $, DIMS, esc, state } from "./core.js";
import { api } from "./api.js";
import { renderLangProgress } from "./curriculum.js";
import { practiceWith } from "./recording.js";
import { voiceSummary } from "./results.js";

/* ------------------------------------------------------------------ progress */
export const tip = $("tooltip");
export function showTip(html, ev) {
  tip.innerHTML = html; tip.classList.remove("hidden");
  const x = Math.min(ev.clientX + 12, innerWidth - tip.offsetWidth - 8);
  tip.style.left = `${x}px`; tip.style.top = `${ev.clientY - tip.offsetHeight - 10}px`;
}
export const hideTip = () => tip.classList.add("hidden");

export async function loadProgress() {
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
    ...(p.weekly?.length ? [[p.weekly[p.weekly.length - 1].overall, "Retained skill (weekly)",
      `<span class="muted">unseen prompt, first take · ${p.weekly.length} week${p.weekly.length > 1 ? "s" : ""}</span>`]] : []),
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

export function renderRecommendation(r) {
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

export function startRecommended(r) {
  practiceWith({ mode: r.mode, prompt: r.prompt, challenge: r.challenge_mode,
    ctx: { kind: "review", title: `Review: ${r.title}`, constraint: r.constraint, steps: r.steps } });
}

export function drawTrend(points) {
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

export function drawDims(cur, prev, labels) {
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
export async function loadHistory() {
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

export function historyAttempt(t) {
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
