/* Thought → Speak — habit.js: the daily goal, streak copy and result headlines. No DOM, no imports
   (unit-tested with node --test, like takes.js). */

export const DAILY_GOAL = 3;            // three takes ≈ one prompt answered, retried, retried again

/** The local calendar day (YYYY-MM-DD) of an ISO timestamp, in the browser's timezone. */
export function localDay(iso) {
  const d = new Date(iso);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** How many takes were saved today (browser's calendar day). */
export function takesToday(history, now = new Date()) {
  const today = localDay(now.toISOString());
  return (history || []).filter((a) => a?.created_at && localDay(a.created_at) === today).length;
}

/** What the day strip says: progress toward the goal, the streak, and one next step. */
export function dayStatus({ today = 0, streak = 0, total = 0, goal = DAILY_GOAL } = {}) {
  const done = today >= goal;
  const left = Math.max(0, goal - today);
  let line;
  if (total === 0) line = "Your first take is the hardest one. Answer one prompt out loud.";
  else if (done) line = `${today} take${today === 1 ? "" : "s"} today — your daily goal is done. Anything more is a bonus.`;
  else if (today === 0) line = streak > 0 ? `Keep your ${streak}-day streak going with one take today.` : "One take today starts a streak.";
  else line = `${today} of ${goal} takes today — ${left} more to reach your daily goal.`;
  return { done, left, goal, today: Math.min(today, goal), extra: Math.max(0, today - goal), streak, line,
           streakLabel: streak === 1 ? "1 day" : `${streak} days` };
}

/** True only on the save that reaches the goal, so the celebration shows once per day. */
export function justReachedGoal(before, after, goal = DAILY_GOAL) {
  return before < goal && after >= goal;
}

/** One encouraging, honest line above the score. `delta` is the change from the previous take, if any. */
export function scoreHeadline(score, delta = null) {
  if (typeof delta === "number" && delta >= 3) return `Up ${delta} points on your last take. The retry worked.`;
  if (typeof delta === "number" && delta <= -3) return "A step back this time. That's normal — use the tip below and go again.";
  if (score >= 85) return "Strong answer. Clear, complete and easy to follow.";
  if (score >= 70) return "Good answer. One change will lift it further.";
  if (score >= 55) return "A solid start. The tip below is your quickest win.";
  return "You said it out loud — that's the hard part. Now make one change.";
}
