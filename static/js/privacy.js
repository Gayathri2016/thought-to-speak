/* Thought → Speak — privacy.js: fills in the server's feedback link and contact address. */
fetch("/api/config").then((r) => r.json()).then((c) => {
  const b = c.beta || {};
  const hosted = c.provider !== "local" || (c.transcriber && !["none", "local"].includes(c.transcriber));
  document.getElementById("coachAI").hidden = !hosted;      // shown by default: never under-promise
  document.getElementById("coachLocal").hidden = hosted;
  if (b.feedback_url && /^https?:\/\//.test(b.feedback_url)) document.getElementById("fb").href = b.feedback_url;
  if (b.contact) {
    const mail = document.getElementById("mail"), a = document.createElement("a");
    a.href = `mailto:${b.contact}`;
    a.textContent = b.contact;
    mail.append(" or email ", a);
  }
}).catch(() => {});
