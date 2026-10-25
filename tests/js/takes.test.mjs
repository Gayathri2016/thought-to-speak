// Unit tests for static/js/takes.js (idempotency-key rules). Run: node --test tests/js/
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

// takes.js is a browser ES module in a folder without package.json — load its source as a module on any Node.
const src = readFileSync(new URL("../../static/js/takes.js", import.meta.url), "utf8");
const { createTakeController } = await import(`data:text/javascript,${encodeURIComponent(src)}`);

const httpError = (status) => Object.assign(new Error(`HTTP ${status}`), { status });
const networkError = () => new TypeError("Failed to fetch");                 // fetch() rejects without a status

/** A fake server: one job per idempotency key, like the real API. `script` overrides responses per call. */
function fakeServer({ outcome = "completed", script = [] } = {}) {
  const jobs = new Map(), keys = [];
  let n = 0;
  const submit = async (body, key) => {
    keys.push(key);
    const step = script.shift();
    if (step === "network") throw networkError();
    if (typeof step === "number") throw httpError(step);
    if (!jobs.has(key)) jobs.set(key, { id: `job-${++n}`, status: "queued", final: outcome, body });
    return { ...jobs.get(key) };
  };
  const poll = async (id) => {
    const j = [...jobs.values()].find((x) => x.id === id);
    j.status = j.final;
    return { ...j, result: j.final === "completed" ? { attempt: { id: `att-${id}` } } : null, error: j.final === "failed" ? "boom" : null };
  };
  return { submit, poll, jobs, keys };
}

let k = 0;
const makeKey = () => `take-${++k}`;

test("a failed job clears the key, so trying again creates a new job", async () => {
  const s = fakeServer({ outcome: "failed" });
  const t = createTakeController({ ...s, makeKey });
  const first = await t.run({ transcript: "same" });
  assert.equal(first.status, "failed");
  assert.equal(t.key, null);
  s.jobs.forEach((j) => { j.final = "completed"; });
  const second = await t.run({ transcript: "same" });                       // unchanged input
  assert.notEqual(s.keys[0], s.keys[1]);
  assert.notEqual(second.id, first.id);
  assert.equal(s.jobs.size, 2);
});

test("a completed job clears the key", async () => {
  const s = fakeServer();
  const t = createTakeController({ ...s, makeKey });
  assert.equal((await t.run({})).status, "completed");
  assert.equal(t.key, null);
});

test("a network error on submit keeps the key, so the retry reuses the same job", async () => {
  const s = fakeServer({ script: ["network"] });
  const t = createTakeController({ ...s, makeKey });
  await assert.rejects(t.run({}), TypeError);
  const kept = t.key;
  assert.ok(kept);
  const done = await t.run({});
  assert.deepEqual(s.keys, [kept, kept]);
  assert.equal(done.status, "completed");
  assert.equal(s.jobs.size, 1);
});

test("a server error (5xx) or timeout keeps the key — the outcome is unknown", async () => {
  for (const status of [500, 502, 503, 408]) {
    const s = fakeServer({ script: [status] });
    const t = createTakeController({ ...s, makeKey });
    await assert.rejects(t.run({}));
    assert.ok(t.key, `key kept after ${status}`);
    await t.run({});
    assert.equal(s.keys[0], s.keys[1]);
  }
});

test("a dropped connection while polling keeps the key and re-attaches to the same job", async () => {
  const s = fakeServer();
  let failPoll = true;
  const poll = async (id) => { if (failPoll) { failPoll = false; throw networkError(); } return s.poll(id); };
  const t = createTakeController({ submit: s.submit, poll, makeKey });
  await assert.rejects(t.run({}));
  assert.ok(t.key);
  const done = await t.run({});
  assert.equal(s.keys[0], s.keys[1]);
  assert.equal(done.id, "job-1");
  assert.equal(s.jobs.size, 1);
});

test("a definite rejection (4xx) clears the key — no job exists", async () => {
  for (const status of [400, 401, 403, 404, 413, 422, 429]) {
    const s = fakeServer({ script: [status] });
    const t = createTakeController({ ...s, makeKey });
    await assert.rejects(t.run({}));
    assert.equal(t.key, null, `key cleared after ${status}`);
  }
});

test("409 (key used for a different submission) retries once with a new key", async () => {
  const s = fakeServer({ script: [409] });
  const t = createTakeController({ ...s, makeKey });
  const done = await t.run({});
  assert.equal(s.keys.length, 2);
  assert.notEqual(s.keys[0], s.keys[1]);
  assert.equal(done.status, "completed");
});

test("double-clicks and retries while queued/running never create a second job", async () => {
  const s = fakeServer();
  let release;
  const gate = new Promise((r) => { release = r; });
  const poll = async (id) => { await gate; return s.poll(id); };
  const t = createTakeController({ submit: s.submit, poll, makeKey });
  const a = t.run({}), b = t.run({}), c = t.run({});
  assert.equal(t.busy, true);
  assert.equal(t.resume("job-1"), a);                                       // resume also joins the in-flight run
  t.reset();                                                                // clearing mid-flight doesn't drop the key
  assert.ok(t.key);
  release();
  const [ra, rb, rc] = await Promise.all([a, b, c]);
  assert.equal(s.keys.length, 1);
  assert.equal(ra, rb);
  assert.equal(rb, rc);
  assert.equal(t.busy, false);
});

test("a deleted take (completed without a result) is terminal: the key is cleared", async () => {
  const submit = async () => ({ id: "job-9", status: "completed", result: null, error_code: "deleted", error: "deleted" });
  const t = createTakeController({ submit, poll: async () => { throw new Error("no poll needed"); }, makeKey });
  const done = await t.run({});
  assert.equal(done.error_code, "deleted");
  assert.equal(t.key, null);
});

test("resume() after a reload clears the key once the job is terminal", async () => {
  const t = createTakeController({ submit: async () => { throw new Error("unused"); },
    poll: async (id) => ({ id, status: "failed", error: "x" }), makeKey });
  assert.equal((await t.resume("job-1")).status, "failed");
  assert.equal(t.key, null);
  assert.equal(t.busy, false);
});
