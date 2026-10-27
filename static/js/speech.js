/* Keep each final recognition segment once, and drain the last results before analyzing.
   Interim text is a preview, never silently treated as confirmed words. No DOM dependency. */
export function createSpeechSession({ recognition, append, preview, ended = () => {},
  error = () => {}, timedOut = () => {}, drainMs = 1800 }) {
  let active = false;
  let pending = null;
  let finish = null;
  let timer = null;
  let acceptResults = false;
  const finals = new Set();

  function settle() {
    clearTimeout(timer);
    const resolve = finish;
    pending = null; finish = null;
    preview("");
    resolve?.();
  }
  recognition.onresult = (event) => {
    if (!acceptResults) return;
    let confirmed = "", interim = "";
    for (let i = 0; i < event.results.length; i++) {
      const result = event.results[i];
      if (result.isFinal) {
        if (!finals.has(i)) {
          finals.add(i);
          confirmed += result[0].transcript + " ";
        }
      } else {
        interim += result[0].transcript + " ";
      }
    }
    if (confirmed.trim()) append(confirmed.trim());
    preview(interim.trim());
  };
  recognition.onerror = error;
  recognition.onend = () => { active = false; settle(); ended(); };

  return {
    start() {
      if (pending || active) return;
      finals.clear();
      acceptResults = true;
      active = true;
      try { recognition.start(); } catch (e) { active = false; throw e; }
    },
    discard() { acceptResults = false; preview(""); },
    stop() {
      if (pending) return pending;
      if (!active) return Promise.resolve();
      pending = new Promise((resolve) => { finish = resolve; });
      const done = pending;
      timer = setTimeout(() => {
        // Keep final segments received so far; never invent the unconfirmed ending.
        acceptResults = false;
        try { recognition.abort?.(); } catch (_) {}
        timedOut();
        settle();
      }, drainMs);
      try { recognition.stop(); } catch (_) { active = false; settle(); }
      return done;
    },
  };
}
