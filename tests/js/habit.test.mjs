// Unit tests for static/js/habit.js (daily goal, streak copy, result headlines). Run: node --test tests/js/
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const src = readFileSync(new URL("../../static/js/habit.js", import.meta.url), "utf8");
const { DAILY_GOAL, takesToday, dayStatus, justReachedGoal, scoreHeadline } =
  await import(`data:text/javascript,${encodeURIComponent(src)}`);

test("takes today counts only today's takes in the local calendar", () => {
  const now = new Date(2026, 9, 25, 18, 0);                      // Oct 25, 6pm local
  const at = (d, h) => ({ created_at: new Date(2026, 9, d, h, 0).toISOString() });
  assert.equal(takesToday([at(25, 9), at(25, 17), at(24, 23), at(26, 0)], now), 2);
  assert.equal(takesToday([], now), 0);
  assert.equal(takesToday(null, now), 0);
});

test("day status walks a new user from first take to goal", () => {
  assert.match(dayStatus({ total: 0 }).line, /first take/);
  assert.match(dayStatus({ total: 4, today: 0, streak: 0 }).line, /starts a streak/);
  assert.match(dayStatus({ total: 4, today: 0, streak: 5 }).line, /5-day streak/);
  const one = dayStatus({ total: 4, today: 1, streak: 1 });
  assert.equal(one.left, DAILY_GOAL - 1);
  assert.match(one.line, /1 of 3 takes today — 2 more/);
  assert.match(dayStatus({ total: 4, today: 2, streak: 1 }).line, /1 more to reach/);
  assert.match(dayStatus({ total: 9, today: 5 }).line, /5 takes today — your daily goal is done/);
  const done = dayStatus({ total: 9, today: 5, streak: 2 });
  assert.equal(done.done, true);
  assert.equal(done.today, DAILY_GOAL);
  assert.equal(done.extra, 2);
  assert.equal(dayStatus({ streak: 1 }).streakLabel, "1 day");
});

test("the goal celebration fires once, on the take that reaches it", () => {
  assert.equal(justReachedGoal(2, 3), true);
  assert.equal(justReachedGoal(3, 4), false);
  assert.equal(justReachedGoal(0, 1), false);
});

test("score headlines are honest and point at the next step", () => {
  assert.match(scoreHeadline(90), /Strong/);
  assert.match(scoreHeadline(72), /One change/);
  assert.match(scoreHeadline(59), /solid start/);
  assert.match(scoreHeadline(30), /out loud/);
  assert.match(scoreHeadline(60, 8), /Up 8 points/);
  assert.match(scoreHeadline(60, -5), /step back/);
  assert.match(scoreHeadline(60, 1), /solid start/);              // small changes don't claim progress
});
