/* Thought → Speak — results.js: Results: focused feedback, scores and provenance, the full review, voice and language reports, comparisons. */
import { $, DIMS, esc, fmtTime, state } from "./core.js";
import { api } from "./api.js";
import { practiceWith, startRetry } from "./recording.js";
import { scoreHeadline } from "./habit.js";

export function renderAnalysis(a) {
  $("emptyState").classList.add("hidden"); $("results").classList.remove("hidden");
  $("warning").textContent = a.warning || ""; $("warning").classList.toggle("hidden", !a.warning);
  revealScore(a.overall_score);
  $("scoreHeadline").textContent = scoreHeadline(a.overall_score);
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
    [m.filler_rate, "transcript fillers / 100w", "measured"],
    [m.hedge_count, "hedges", "measured"],
  ].map(([v, l, k]) => `<div class="metric ${k}" title="${k === "estimated" ? "Estimated from word count (no recording timing)" : "Counted from your transcript or measured from your recording"}">
    <b>${esc(v)}${k === "estimated" ? "<sup>est</sup>" : ""}</b><span>${esc(l)}</span></div>`).join("");
  $("provenanceLine").innerHTML = provenanceLine(pv);
  finishAnalysis(a);
}

/* The one celebratory motion in the app: the ring draws and the number counts up as the result arrives. */
function revealScore(score) {
  const C = 2 * Math.PI * 52, fg = $("ringFg"), out = $("overall");
  const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
  fg.style.strokeDasharray = C;
  $("ring").dataset.band = score >= 70 ? "high" : score >= 55 ? "mid" : "low";
  if (calm) { fg.style.strokeDashoffset = C * (1 - score / 100); out.textContent = score; return; }
  fg.style.transition = "none"; fg.style.strokeDashoffset = C; out.textContent = "0";
  void fg.getBoundingClientRect();                        // restart the draw from empty
  fg.style.transition = ""; fg.style.strokeDashoffset = C * (1 - score / 100);
  const t0 = performance.now(), dur = 800;
  const step = (t) => {
    const k = Math.min(1, (t - t0) / dur), eased = 1 - Math.pow(1 - k, 3);
    out.textContent = Math.round(score * eased);
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

/* ------------------------------------------------------------------ focused results (v8.3) */
export const fmtTs = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
export function evidenceHtml(x) {
  if (!x || !x.quote) return "";
  const ts = x.start != null ? ` <button class="ts" data-t="${x.start}" title="Play this moment">▶ ${fmtTs(x.start)}</button>` : "";
  return `<p class="fc-quote"><q>${esc(x.quote)}</q>${ts}</p>`;
}
export function renderFocus(a) {
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
      ${starterHtml(a)}
      <button class="primary sm" id="fcRetry">🔁 Retry now</button></div></div>
    ${sayItHtml(a, "fc")}`;
  $("fcRetry").onclick = () => { startRetry(); $("status").textContent = `Focus: ${f.retry}`; };
  wireSayIt(a, "fc");
  // the retry instruction replaces the generic "next challenge" text; the buttons below stay
  $("nextChallenge").classList.add("hidden"); $("nextLabel").textContent = "Keep going";
}
/* ------------------------------------------------------------------ the mentor: "say it like this" */
function starterHtml(a) {
  const st = a.review?.off_topic?.starter;
  return st && !a.challenge_mode ? `<p class="fc-starter"><span>To answer the prompt, start like this:</span> “${esc(st)}”</p>` : "";
}
export function sayItHtml(a, id) {
  const co = a.review?.coach || {};
  if (a.challenge_mode || !co.stronger_version) return "";
  const parts = co.script || null;
  const ai = a.review?.source === "ai";
  const spoken = (parts ? parts.filter((p) => !p.fill_in).map((p) => p.text).join(" ") : co.stronger_version) || "";
  const secs = Math.max(5, Math.round(spoken.split(/\s+/).filter(Boolean).length / 2.5 / 5) * 5);   // ~150 wpm
  const tag = (p) => p.yours ? (ai ? "from your words" : "your words") : p.fill_in ? "add this" : "suggested";
  const body = parts ? `<ol class="say-parts">${parts.map((p) => `<li class="${p.yours ? "yours" : p.fill_in ? "fill" : "added"}">
      <span class="say-label">${esc(p.label)}<em>${tag(p)}</em></span>
      <span class="say-text">${esc(p.text)}</span></li>`).join("")}</ol>`
    : `<p class="say-text">${esc(co.stronger_version)}</p>`;
  const intro = !parts ? "Same facts, better shape."
    : `Your answer in ${esc(a.framework_name || "the scenario")} order (about ${secs} seconds). ` +
      (ai ? "Lines marked “suggested” fill a missing part — say them your way."
          : "Lines marked “add this” are for you to fill in — say them your way.");
  return `<div class="fc-row say" id="${id}Say"><span class="fc-ico" aria-hidden="true">💬</span><div>
    <span class="eyebrow">Say it like this</span>
    <p class="small muted say-intro">${intro}</p>
    ${body}
    ${id === "fc" ? grammarLine(a.review?.corrections) : ""}
    <div class="row gap-s wrap-row"><button class="ghost sm" id="${id}Hear">▶ Listen</button>
      ${id === "fc" ? `<button class="link-btn sm" id="fcLines">See each fix, line by line</button>` : ""}</div></div></div>`;
}
const KIND_LABEL = { tense: "past tense", agreement: "subject–verb agreement", "verb form": "verb forms",
  pronouns: "pronouns", nouns: "plurals", "word choice": "word choice", capitals: "capital letters" };
function grammarLine(c) {
  const g = c?.grammar || [], n = c?.grammar_count || 0;
  if (!n || !g.length) return "";
  return `<p class="say-grammar"><b>📐 ${n} grammar fix${n > 1 ? "es" : ""}</b> — mostly ${esc(KIND_LABEL[g[0].kind] || g[0].kind)}. ${esc(g[0].lesson)}</p>`;
}
export function grammarHtml(c, challenge) {
  const g = c?.grammar || [];
  if (!g.length) return "";
  return `<div class="gram"><div class="eyebrow">📐 Grammar to practise</div><ul class="gram-list">${g.map((p) => `<li>
      <b>${esc((KIND_LABEL[p.kind] || p.kind).replace(/^./, (x) => x.toUpperCase()))}</b>${p.count > 1 ? ` <span class="muted small">×${p.count}</span>` : ""}
      <span>${esc(p.lesson)}</span>
      ${!challenge && p.examples?.length ? `<span class="gram-ex">${p.examples.map((e) => `<code>${esc(e)}</code>`).join(" ")}</span>` : ""}</li>`).join("")}</ul></div>`;
}
export function wireSayIt(a, id) {
  const co = a.review?.coach || {};
  if ($(`${id}Hear`)) $(`${id}Hear`).onclick = () => speak(co.stronger_version, 0.95);
  if ($("fcLines") && id === "fc") $("fcLines").onclick = () => {
    const d = $("moreReview"); d.open = true;
    (document.getElementById("rvLines") || d).scrollIntoView({ behavior: "smooth", block: "start" });
  };
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
export function syncMore() {
  const hasContent = { moreVoice: !$("voiceCard").classList.contains("hidden"),
    moreLanguage: !$("languageCard").classList.contains("hidden") };
  Object.entries(hasContent).forEach(([id, on]) => $(id).classList.toggle("hidden", !on));
}
document.querySelectorAll("details.more").forEach((d) => {
  try { d.open = localStorage.getItem(`tts-more-${d.dataset.key}`) === "1"; } catch (_) {}
  d.addEventListener("toggle", () => { try { localStorage.setItem(`tts-more-${d.dataset.key}`, d.open ? "1" : "0"); } catch (_) {} });
});

/* ------------------------------------------------------------------ scoring provenance (v8.2) */
export function provBadge(src, dim) {
  if (src === "ai") return `<span class="pb ai" title="Judged by the AI coach">AI</span>`;
  if (dim === "delivery") return `<span class="pb est" title="Built-in formula over measured signals: fillers, hedges, pace, pauses and pitch">formula</span>`;
  return `<span class="pb est" title="Estimated by the built-in coach">est.</span>`;
}
export function provenanceLine(pv) {
  if (!pv.scoring_version) return "";
  return `Scored by ${esc(pv.scorer || pv.scoring_version)} · transcript: ${esc(pv.transcript_source || "unknown")}` +
    `${pv.rubric_version ? ` · rubric ${esc(pv.rubric_version)}` : ""}<br>` +
    `<span class="pb ai">AI</span> judged by the AI coach · <span class="pb est">est.</span> built-in estimate · ` +
    `numbers without a tag are measured from your transcript or recording.`;
}

export function finishAnalysis(a) {
  const f = Object.entries(a.metrics.fillers || {});
  $("fillerChips").innerHTML = f.length
    ? f.map(([k, v]) => `<span class="chip">“${esc(k)}” ×${v}</span>`).join("")
    : `<span class="chip">No fillers found in the transcript</span>`;
  $("fillerNote").textContent = a.metrics.filler_note || "Counts describe the transcript. Speech recognition may omit fillers.";

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
export function renderReview(a) {
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
    ${a.challenge_mode ? "" : `<section class="rv-sec rv-say"><div class="rv-h"><span class="rv-who">💬 Say it like this</span>
      <span class="small muted">your answer, rewritten from your own words</span></div>${sayItHtml(a, "rv")}</section>`}

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
      ${!a.challenge_mode && !co.stronger_version && outline.length ? `<details class="rv-drill"><summary>🦴 Skeleton built from your own words</summary>
        <ul class="small">${outline.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></details>` : ""}
    </section>

    <section class="rv-sec rv-mentor">
      <div class="rv-h"><span class="rv-who">🧭 Mentor</span><span class="small muted">${me.based_on ? `based on your last ${me.based_on} takes` : "the bigger picture"}</span></div>
      ${me.pattern ? `<p><b>Pattern:</b> ${esc(me.pattern)}</p>` : ""}
      ${me.improving ? `<p><b>Improving:</b> ${esc(me.improving)}</p>` : ""}
      <p><b>This week's habit:</b> ${esc(me.habit)}</p>
      ${me.note ? `<p class="small muted">${esc(me.note)}</p>` : ""}
    </section>
    <p class="small muted rv-src">${r.source === "ai" ? "Review written by your AI coach." : "Review from the built-in coach."}
      Every quote was checked against your transcript${r.evidence?.dropped ? ` (${r.evidence.dropped} unverifiable quote${r.evidence.dropped > 1 ? "s" : ""} removed)` : ""}${r.evidence?.timestamps ? "; ▶ times come from Whisper's word timings" : ""}.</p>`;
  $("rvFollow").onclick = () => {
    state.chainId = null; state.takeNo = 1;
    practiceWith({ mode: a.mode in state.modes ? a.mode : state.mode, prompt: ev.follow_up });
    $("transcript").value = ""; $("status").textContent = "Follow-up question loaded — tap the mic to answer.";
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  $("rvRetry").onclick = () => {
    startRetry();
    if (co.do_next?.length) $("status").textContent = `Focus: ${co.do_next[0]}`;
  };
  wireSayIt(a, "rv");
  if ($("rvHearFixed")) $("rvHearFixed").onclick = () => speak(r.corrections.corrected_full, 0.95);
}

export function renderCorrections(c, challenge) {
  const items = c?.items || [];
  if (!items.length) return `<section class="rv-sec rv-fix" id="rvLines"><div class="rv-h"><span class="rv-who">✍️ Line by line</span></div>
    ${grammarHtml(c, challenge)}<p class="small muted">No corrections needed — your sentences came through clean.</p></section>`;
  return `<section class="rv-sec rv-fix" id="rvLines">
    <div class="rv-h"><span class="rv-who">✍️ Line by line</span><span class="small muted">${challenge ? "find the fix yourself" : "what you said → how to say it → why"}</span></div>
    ${grammarHtml(c, challenge)}
    ${c.split_from_run_on ? `<p class="small rv-note">Your answer came through as one long sentence. Here it is as ${c.split_from_run_on} sentences — one idea each.</p>` : ""}
    <ol class="rv-corr">${items.map((x) => `<li>
      <div class="rv-said"><span class="eyebrow">You said</span><span><q>${esc(x.original)}</q>${x.start != null ? ` <button class="ts" data-t="${x.start}">▶ ${fmtTs(x.start)}</button>` : ""}</span></div>
      ${x.corrected ? `<div class="rv-better"><span class="eyebrow">Say</span><span>${esc(x.corrected)}</span></div>` : ""}
      ${(x.changes || []).length || (x.notes || []).length ? `<ul class="rv-why">${[...(x.changes || []), ...(x.notes || [])]
        .map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}</li>`).join("")}</ol>
    ${c.corrected_full ? `<details class="rv-drill"><summary>📝 Your whole answer, corrected</summary>
      <p class="rv-strong">${esc(c.corrected_full)}</p><button class="ghost sm" id="rvHearFixed">▶ Listen</button>
      <p class="small muted">Same words and meaning — just fixed. Read it aloud once, then answer again from memory.</p></details>` : ""}
    <p class="small muted">If a line has words you didn't say, the browser misheard you — fix them in “What you said” and get feedback again.</p>
  </section>`;
}

export function deltaCell(d) {
  if (d === null || d === undefined) return `<td class="delta flat">—</td>`;
  const cls = d >= 3 ? "up" : d <= -3 ? "down" : "flat";
  const arrow = d >= 3 ? "▲" : d <= -3 ? "▼" : "•";
  return `<td class="delta ${cls}">${arrow} ${d > 0 ? "+" : ""}${d}</td>`;
}

export function fillerDeltaCell(d) {  // fewer fillers is better
  if (d === null || d === undefined) return `<td class="delta flat">—</td>`;
  const cls = d < 0 ? "up" : d > 0 ? "down" : "flat";
  return `<td class="delta ${cls}">${d < 0 ? "▼" : d > 0 ? "▲" : "•"} ${d > 0 ? "+" : ""}${d}</td>`;
}

export async function renderComparison(c, chainId) {
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

/* ------------------------------------------------------------------ voice report (v4) */
export function voiceSummary(v) {
  const p = v.pitch || {};
  return [
    v.speaking_rate_wpm ? `${v.speaking_rate_wpm} wpm overall (${v.articulation_rate_wpm} while talking)` : null,
    `${v.pause_count} pauses, ${v.stalls} stall${v.stalls === 1 ? "" : "s"}, longest ${v.longest_pause}s`,
    p.range_semitones != null ? `pitch range ${p.range_semitones} semitones (${p.label})` : null,
    v.volume.trails_off ? `volume drops ${v.volume.trailing_drop_db} dB at the end` : "volume steady",
    `${Math.round(v.silence_ratio * 100)}% silence`,
  ].filter(Boolean).join(" · ");
}

export function renderVoice(v, attemptId) {
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

export function drawWaveform(v) {
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

export function drawPace(pace) {
  const el = $("paceChart");
  if (!pace.length) { el.innerHTML = `<p class="small muted">Too short to chart.</p>`; return; }
  const W = 300, H = 60, max = 230, y = (v) => H - 10 - (Math.min(v, max) / max) * (H - 14);
  const bw = W / pace.length;
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Words per minute every 10 seconds">
    <rect class="band" x="0" y="${y(165)}" width="${W}" height="${y(130) - y(165)}"><title>130–165 wpm target</title></rect>
    ${pace.map((p, i) => `<rect class="pb" x="${i * bw + 2}" y="${y(p.wpm)}" width="${Math.max(2, bw - 4)}" height="${H - 10 - y(p.wpm)}" rx="2"><title>${p.t}s: ${p.wpm} wpm</title></rect>`).join("")}
    <text x="2" y="${H - 1}">green band = 130–165 wpm</text></svg>`;
}

export function drawPitch(contour, dur) {
  const el = $("pitchChart");
  if (contour.length < 5) { el.innerHTML = `<p class="small muted">Not enough voiced audio.</p>`; return; }
  const W = 300, H = 60, y = (s) => H / 2 - (Math.max(-8, Math.min(8, s)) / 8) * (H / 2 - 4);
  const T = dur || contour[contour.length - 1][0] || 1;
  const pts = contour.map(([t, s]) => `${((t / T) * W).toFixed(1)},${y(s).toFixed(1)}`).join(" ");
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Pitch contour in semitones">
    <line class="zero" x1="0" x2="${W}" y1="${H / 2}" y2="${H / 2}"/><polyline class="pl2" points="${pts}"/>
    <text x="2" y="9">+8 st</text><text x="2" y="${H - 2}">−8 st</text></svg>`;
}

/* ------------------------------------------------------------------ pronunciation & language (v5) */
export const lookupCache = {};
export async function lookup(words) {
  const need = words.filter((w) => !(w in lookupCache));
  if (need.length) (await api(`/api/pronounce/lookup?words=${encodeURIComponent(need.join(","))}`))
    .forEach((d, i) => (lookupCache[need[i]] = d));
  return words.map((w) => lookupCache[w]);
}
export const normWord = (w) => w.toLowerCase().replace(/[’]/g, "'").replace(/[^a-z0-9']/g, "").replace(/^'+|'+$/g, "");

export function wordsHtml(text, focus = [], statusByIndex = null) {
  const f = new Set(focus.map(normWord));
  let i = 0;
  return esc(text).replace(/[A-Za-z][A-Za-z'’]*/g, (w) => {
    const n = normWord(w);
    const st = statusByIndex ? statusByIndex[i] : null; i++;
    return `<span class="w ${f.has(n) ? "focus" : ""} ${st ? "st-" + st : ""}" data-w="${esc(n)}" tabindex="0">${w}</span>`;
  });
}

export let voiceCache = null;
export function speak(text, rate = 0.9) {
  if (!("speechSynthesis" in window)) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = "en-US"; u.rate = rate;
  voiceCache = voiceCache || speechSynthesis.getVoices().find((v) => /en[-_]US/i.test(v.lang));
  if (voiceCache) u.voice = voiceCache;
  speechSynthesis.speak(u);
}

export async function showWordPop(word, anchor) {
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

export function renderPronunciation(a) {
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

export function scoreBar(label, v) {
  return `<div class="dim"><span>${esc(label)}</span><div class="dim-bar"><div style="width:${v ?? 0}%"></div></div><b>${v ?? "—"}</b></div>`;
}

export function renderLanguage(a) {
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
