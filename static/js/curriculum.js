/* Thought → Speak — curriculum.js: Learning content: Today's session, journeys, lessons, the Speaking Sprint and the Pronounce tab. */
import { $, esc, state } from "./core.js";
import { api } from "./api.js";
import { showTab } from "./main.js";
import { READ_ALOUD, practiceWith, startRetry } from "./recording.js";
import { speak, wordsHtml } from "./results.js";

/* ------------------------------------------------------------------ learn (v3) */
export function renderLessonResult(lr, program) {
  const el = $("lessonResult");
  const c = state.ctx;
  if (!lr) {
    if (c && ["warmup", "review", "baseline", "weekly"].includes(c.kind)) {
      const msg = { warmup: "Warm-up done for today", review: "Review done — your skill schedule is updated",
        baseline: "Baseline take recorded", weekly: "Weekly challenge recorded — this measures retained skill" }[c.kind];
      el.className = "lesson-result pass";
      el.innerHTML = `<h4>✓ ${msg}</h4>
        <button class="ghost sm" id="lrToday">Back to Learn</button>`;
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

export async function loadLearn() {
  const [today, journeys, programs, baseline, skills, weekly] = await Promise.all([api("/api/today"),
    api("/api/journey"), api("/api/programs"), api("/api/baseline"), api("/api/skills"), api("/api/weekly")]);
  state.todayWords = today.words || [];
  renderBaseline(baseline);
  renderSkills(skills, baseline);
  renderWeekly(weekly);
  renderPrograms(programs);
  renderToday(today);
  renderJourneys(journeys);
  if (!state.lesson || $("lessonView").classList.contains("hidden")) showLearnHome();
}
/* ------------------------------------------------------------------ adaptive learning (v9) */
function renderBaseline(b) {
  const el = $("baselineCard");
  if (b.complete) { el.innerHTML = ""; return; }
  const done = b.items.filter((i) => i.done).length;
  el.innerHTML = `<section class="card baseline">
    <div class="row between wrap-row"><div><span class="eyebrow">Start here · about ${b.minutes} minutes</span>
      <div class="card-h" style="margin:2px 0 4px">Your baseline: three short answers</div>
      <p class="small muted" style="margin:0">They set your starting level for each skill, so reviews focus on what you actually need.</p></div>
      <b>${done}/${b.items.length}</b></div>
    <div class="bl-items">${b.items.map((i, n) => `<div class="bl-item ${i.done ? "done" : ""}">
      <span class="ti-check">${i.done ? "✓" : n + 1}</span><div><b>${esc(i.title)}</b><p class="small">“${esc(i.prompt)}”</p></div>
      ${i.done ? `<span class="small muted">Done</span>` : `<button class="primary sm" data-bl="${n}">Start</button>`}</div>`).join("")}</div>
  </section>`;
  el.querySelectorAll("[data-bl]").forEach((btn) => (btn.onclick = () => {
    const i = b.items[+btn.dataset.bl];
    practiceWith({ mode: i.mode, prompt: i.prompt, ctx: { kind: "baseline", title: `Baseline: ${i.title}`,
      constraint: "Answer naturally — this is a starting point, not a test.", steps: [], target: null } });
  }));
}

function renderSkills(s, baseline) {
  const el = $("skillsCard");
  const dots = (box, boxes) => Array.from({ length: boxes }, (_, i) => `<i class="${i < box ? "on" : ""}"></i>`).join("");
  el.innerHTML = `<div class="card-h" style="margin-bottom:4px">Skill reviews</div>
    <p class="small muted" style="margin:0 0 10px">${esc(s.rule)}</p>
    ${baseline.summary ? `<p class="small sk-base">${esc(baseline.summary.text)}</p>` : ""}
    <div class="sk-list">${s.skills.map((k) => `<div class="sk ${k.status}" title="${esc(k.why)}">
      <div class="sk-h"><b>${esc(k.label)}</b><span class="small ${k.status === "due" ? "due" : "muted"}">${
        k.status === "new" ? "no evidence yet" : k.status === "due" ? "due today" : `in ${k.due_in_days} day${k.due_in_days === 1 ? "" : "s"}`}</span></div>
      ${k.box ? `<div class="sk-box" aria-label="Box ${k.box} of ${k.boxes}">${dots(k.box, k.boxes)}<span class="small muted">box ${k.box}/${k.boxes} · last ${k.last_score}</span></div>` : ""}
      <div class="small muted sk-why">${esc(k.why)}</div></div>`).join("")}</div>`;
}

function renderWeekly(w) {
  const el = $("weeklyCard");
  const hist = w.history || [];
  const prev = hist.filter((h) => h.week !== w.week && h.comparable !== false).slice(-1)[0];
  const delta = w.done && prev ? w.score - prev.overall : null;
  el.innerHTML = `<div class="row between wrap-row"><div class="card-h" style="margin:0">Weekly challenge</div>
      <span class="tag" title="${esc(w.week)}">${w.done ? "✓ Done this week" : "This week"}</span></div>
    <p class="small muted" style="margin:6px 0 10px">${esc(w.why)}</p>
    <p class="wk-prompt">“${esc(w.prompt)}”</p>
    <div class="row between wrap-row"><span class="small muted">${esc(w.mode)}</span>
      ${w.done ? `<span><b>${w.score}</b>/100${delta != null ? ` <span class="delta ${delta >= 0 ? "up" : "down"}">${delta >= 0 ? "▲ +" : "▼ "}${delta} vs last week</span>` : ""}</span>`
        : `<button class="primary sm" id="wkStart">Take the challenge</button>`}</div>
    ${hist.length > 1 ? `<div class="wk-hist">${hist.slice(-8).map((h) => `<div class="wk-bar ${h.comparable === false ? "faded" : ""}" title="${esc(h.week)}: ${h.overall}${h.comparable === false ? " (different scorer)" : ""}"><span style="height:${h.overall}%"></span></div>`).join("")}</div>
      <p class="small muted" style="margin:4px 0 0">First takes of unseen prompts, week by week. Faded = scored differently.</p>` : ""}`;
  if ($("wkStart")) $("wkStart").onclick = () => practiceWith({ mode: w.mode, prompt: w.prompt,
    ctx: { kind: "weekly", title: "Weekly challenge (unseen prompt)", constraint: "One take — your first take is the one that counts.",
      steps: [], target: null } });
}

export function showLearnHome() { $("learnHome").classList.remove("hidden"); $("lessonView").classList.add("hidden"); }

export function renderToday(t) {
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
export function renderPrograms(list) {
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

export function renderJourneys(list) {
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

export async function openLesson(id) {
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

/* ------------------------------------------------------------------ pronounce tab */
export async function loadPronounce() {
  if (!state.sets) state.sets = await api("/api/pronounce/sets");
  const prog = await api("/api/progress");
  state.langProg = prog.language || {};
  $("setChips").innerHTML = state.sets.map((st) => `<button class="chip ${st.id === (state.setId || state.sets[0].id) ? "active" : ""}" data-set="${esc(st.id)}">${esc(st.title)}</button>`).join("");
  $("setChips").querySelectorAll("[data-set]").forEach((b) => (b.onclick = () => { state.setId = b.dataset.set; loadPronounce(); }));
  renderSet(state.sets.find((x) => x.id === state.setId) || state.sets[0]);
  renderSoundSummary(state.langProg);
}

export function renderSet(st) {
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

export function renderSoundSummary(lp) {
  const sounds = Object.entries(lp.sounds || {});
  $("soundSummary").innerHTML = sounds.length ? `<div class="eyebrow">Sound groups — words recognised (estimate)</div>` + sounds.sort((a, b) => a[1].accuracy - b[1].accuracy)
    .map(([g, v]) => `<div class="fbar"><span>${esc(g)}</span><div class="t"><div style="width:${v.accuracy}%;${v.accuracy < 85 ? "background:var(--bad)" : ""}"></div></div><b>${v.accuracy}%</b></div>`).join("")
    : `<span class="small muted">Sound-group results appear after a few drills.</span>`;
}

export function renderLangProgress(lp) {
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
