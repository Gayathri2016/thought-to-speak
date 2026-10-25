/* Thought → Speak — takes.js: one take → at most one analysis job. No DOM, no imports (unit-tested with node --test).

   Idempotency-key rules
   - A take keeps one key until the server has definitely finished with it. Re-clicking Analyze, or retrying after a
     dropped connection, re-sends the same key, so the server returns the same job instead of starting (and
     charging for) a second one.
   - Terminal job (completed, failed, or completed-but-deleted) → the key is cleared: the next Analyze is a new
     submission. (Before v9.1 a failed job kept its key, so "try again" kept returning the same failed job.)
   - The server rejected the request (4xx: validation, quota, sign-in) → no job exists → the key is cleared.
   - Outcome unknown (network error, 5xx, 408, or polling failed) → the key is kept: a retry finds the job the
     server may already have created.
   - 409 = this key was used for a different submission (the take was edited after an unknown outcome) → a new key.
   - While a submission is in flight (queued/running), further calls return the same promise: no second job. */

export function newIdemKey() {
  const c = globalThis.crypto;
  return `take-${c?.randomUUID ? c.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`}`;
}

export function isTerminal(job) {
  return job?.status === "completed" || job?.status === "failed";
}

/** The server answered and created no job (so the key can't refer to anything). */
export function definitelyRejected(err) {
  const s = err?.status;
  return typeof s === "number" && s >= 400 && s < 500 && s !== 408 && s !== 409;
}

export function createTakeController({ submit, poll, onJob = () => {}, makeKey = newIdemKey }) {
  let key = null;
  let inFlight = null;

  function track(work) {
    inFlight = (async () => {
      try {
        const done = await work();
        if (isTerminal(done)) key = null;
        return done;
      } catch (e) {
        if (definitelyRejected(e)) key = null;
        throw e;                                   // unknown outcome: keep the key for the retry
      } finally {
        inFlight = null;
      }
    })();
    return inFlight;
  }

  return {
    get key() { return key; },
    get busy() { return inFlight !== null; },
    /** A new take (new prompt, cleared transcript, retry): the next submission gets a new key. */
    reset() { if (!inFlight) key = null; },
    /** Submit this take (or re-attach to its job) and wait for a terminal job. */
    run(body, opts) {
      if (inFlight) return inFlight;
      return track(async () => {
        key = key || makeKey();
        let job;
        try {
          job = await submit(body, key, opts);
        } catch (e) {
          if (e?.status !== 409) throw e;
          key = makeKey();
          job = await submit(body, key, opts);
        }
        onJob(job);
        return isTerminal(job) ? job : poll(job.id);
      });
    },
    /** Pick up a job that was still running before a reload. */
    resume(jobId) {
      if (inFlight) return inFlight;
      return track(() => poll(jobId));
    },
  };
}
