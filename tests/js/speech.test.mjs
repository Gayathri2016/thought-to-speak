import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const src = readFileSync(new URL("../../static/js/speech.js", import.meta.url), "utf8");
const { createSpeechSession } = await import(`data:text/javascript,${encodeURIComponent(src)}`);
const result = (text, isFinal) => Object.assign([{ transcript: text }], { isFinal });

function setup(opts = {}) {
  const final = [], interim = [], recognition = { start() {}, stop() {} };
  const session = createSpeechSession({ recognition, append: (s) => final.push(s),
    preview: (s) => interim.push(s), ...opts });
  session.start();
  return { session, recognition, final, interim };
}

test("stopping waits for late final words before analysis can proceed", async () => {
  const { session, recognition, final } = setup();
  recognition.onresult({ results: [result("Um I was", false)] });
  let analyzed = null;
  const done = session.stop().then(() => { analyzed = final.join(" "); });
  assert.equal(analyzed, null);
  recognition.onresult({ results: [result("Um I was thinking", true)] });
  recognition.onend();
  await done;
  assert.equal(analyzed, "Um I was thinking");
});

test("repeated result snapshots never duplicate finalized segments", () => {
  const { recognition, final, interim } = setup();
  recognition.onresult({ results: [result("Um hello", true), result("I like", false)] });
  recognition.onresult({ results: [result("Um hello", true), result("I like walking", true)] });
  recognition.onresult({ results: [result("Um hello", true), result("I like walking", true)] });
  assert.deepEqual(final, ["Um hello", "I like walking"]);
  assert.equal(interim.at(-1), "");
});

test("revised interim text is a preview and is never counted as confirmed speech", async () => {
  const { session, recognition, final, interim } = setup();
  recognition.onresult({ results: [result("invented preview", false)] });
  recognition.onresult({ results: [result("different preview", false)] });
  const stopped = session.stop();
  recognition.onend();
  await stopped;
  assert.deepEqual(final, []);
  assert.equal(interim.at(-1), "");
});

test("a new recognition session accepts result index zero again", () => {
  const { session, recognition, final } = setup();
  recognition.onresult({ results: [result("first session", true)] });
  recognition.onend();
  session.start();
  recognition.onresult({ results: [result("next session", true)] });
  assert.deepEqual(final, ["first session", "next session"]);
});

test("clearing a take ignores its late final results", async () => {
  const { session, recognition, final } = setup();
  const stopped = session.stop();
  session.discard();
  recognition.onresult({ results: [result("old take ending", true)] });
  recognition.onend();
  await stopped;
  assert.deepEqual(final, []);
});

test("unresponsive recognition times out without turning an interim guess into text", async () => {
  let warnings = 0;
  const { session, recognition, final } = setup({ drainMs: 1, timedOut: () => warnings++ });
  recognition.onresult({ results: [result("unconfirmed words", false)] });
  const a = session.stop(), b = session.stop();
  assert.equal(a, b);
  await a;
  assert.equal(warnings, 1);
  assert.deepEqual(final, []);
  recognition.onresult({ results: [result("too late for this analysis", true)] });
  assert.deepEqual(final, []);
  recognition.onend();
});
