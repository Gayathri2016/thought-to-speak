/* Thought → Speak — auth.js: Accounts: sign-in screen, account menu, sign-out and delete account, app boot. */
import { $, state } from "./core.js";
import { api } from "./api.js";
import { init, toast } from "./main.js";
import { resumeActiveJob } from "./recording.js";

/* ------------------------------------------------------------------ accounts */
export let authView = "signup";
let guestsOn = false, isGuest = false, guestLimit = 3;
const VIEWS = {
  signup: { title: "Create your account", sub: "Practice out loud, get coached on content and voice, and track your progress.",
    submit: "Create account", switchText: "Already have an account?", switchLink: "Sign in" },
  signin: { title: "Welcome back", sub: "Sign in to pick up where you left off.",
    submit: "Sign in", switchText: "New here?", switchLink: "Create an account" },
  upgrade: { title: "Save your progress", sub: "Keep the takes you've done, your streak and your daily goal — free.",
    submit: "Create free account", switchText: "Already have an account?", switchLink: "Sign in" },
};
export function showAuth(view = "signup", message = "") {
  authView = view;
  const v = VIEWS[view], newAccount = view !== "signin";
  document.body.classList.add("signed-out");
  $("authScreen").classList.remove("hidden");
  $("authTitle").textContent = v.title;
  $("authSub").textContent = v.sub;
  $("nameField").classList.toggle("hidden", !newAccount);
  $("pwHint").classList.toggle("hidden", !newAccount);
  $("authPassword").autocomplete = newAccount ? "new-password" : "current-password";
  $("authSubmit").textContent = v.submit;
  $("authSwitchText").textContent = v.switchText;
  $("authSwitch").textContent = v.switchLink;
  $("authError").textContent = message;
  $("authError").classList.toggle("hidden", !message);
  $("authBack").classList.toggle("hidden", !isGuest);            // a guest can go back to practicing
  const offerGuest = guestsOn && !isGuest && view !== "signin";
  $("guestBox").classList.toggle("hidden", !offerGuest);
  $("tryHero").classList.toggle("hidden", !offerGuest);
  if (!offerGuest) setTimeout(() => $(newAccount ? "authName" : "authEmail").focus({ preventScroll: true }), 0);
}
function hideAuth() {
  document.body.classList.remove("signed-out");
  $("authScreen").classList.add("hidden");
}
$("authSwitch").onclick = (e) => { e.preventDefault(); showAuth(authView === "signin" ? (isGuest ? "upgrade" : "signup") : "signin"); };
$("authBack").onclick = (e) => { e.preventDefault(); hideAuth(); };
$("authForm").onsubmit = async (e) => {
  e.preventDefault();
  const btn = $("authSubmit"); btn.disabled = true;
  const body = { email: $("authEmail").value, password: $("authPassword").value };
  if (authView !== "signin") body.name = $("authName").value;
  const path = { signup: "signup", signin: "login", upgrade: "upgrade" }[authView];
  try {
    const r = await api(`/api/auth/${path}`, { method: "POST", body: JSON.stringify(body) });
    $("authPassword").value = "";
    location.reload();          // start the app fresh as this user
    return r;
  } catch (err) {
    $("authError").textContent = err.message; $("authError").classList.remove("hidden");
  } finally { btn.disabled = false; }
};

/* ------------------------------------------------------------------ guests: try first, sign up after */
async function startGuest(btn) {
  btn.disabled = true;
  try {
    await api("/api/auth/guest", { method: "POST" });
    location.reload();
  } catch (err) {
    $("authError").textContent = err.message; $("authError").classList.remove("hidden");
    btn.disabled = false;
  }
}
$("tryBtn").onclick = () => startGuest($("tryBtn"));
$("tryHero").onclick = () => startGuest($("tryHero"));
$("guestSave").onclick = () => showAuth("upgrade");
$("saveAccountBtn").onclick = () => { $("accountPop").classList.add("hidden"); showAuth("upgrade"); };

async function refreshGuestBar() {
  if (!isGuest) return;
  let used = 0;
  try { used = (await api("/api/usage")).used || 0; } catch (_) {}
  const left = Math.max(0, guestLimit - used);
  $("guestBarText").textContent = used === 0
    ? `You're trying Thought → Speak as a guest — ${guestLimit} free takes today.`
    : left > 0 ? `Nice — ${used} take${used > 1 ? "s" : ""} done. ${left} free guest take${left > 1 ? "s" : ""} left today. Save your progress so it isn't lost.`
      : "That's today's free guest takes. Create a free account to keep practicing — your takes come with you.";
  $("guestBar").classList.toggle("done", left === 0);
  $("guestBar").classList.remove("hidden");
}
document.addEventListener("tts:take-done", () => {
  refreshGuestBar();
  if (isGuest) toast("Like it? Save your progress with a free account — your takes come with you.");
});
document.addEventListener("tts:limit", (e) => { if (isGuest) showAuth("upgrade", e.detail || ""); });

export function showAccount(me) {
  if (me.mode !== "accounts" || !me.user) return;
  const u = me.user;
  isGuest = !!u.guest;
  $("accountMenu").classList.remove("hidden");
  $("dangerZone").classList.remove("hidden");
  $("saveAccountBtn").classList.toggle("hidden", !isGuest);
  if (isGuest) {
    state.userName = "";
    $("accountBtn").textContent = "G";
    $("accountBtn").setAttribute("aria-label", "Guest account");
    $("accountName").textContent = "Guest";
    $("accountEmail").textContent = "Not saved to an account yet";
    $("signOutBtn").textContent = "Leave guest mode";
    $("deletePw").classList.add("hidden");
    $("deleteAccountBtn").textContent = "Delete my guest practice";
    refreshGuestBar();
    return;
  }
  const label = (u.name || u.email).trim();
  state.userName = (u.name || "").trim().split(/\s+/)[0] || "";
  $("accountBtn").textContent = label.slice(0, 1).toUpperCase();
  $("accountBtn").setAttribute("aria-label", `Account: ${u.email}`);
  $("accountName").textContent = u.name || "Signed in";
  $("accountEmail").textContent = u.email;
}
$("accountBtn").onclick = (e) => {
  e.stopPropagation();
  const open = $("accountPop").classList.toggle("hidden") === false;
  $("accountBtn").setAttribute("aria-expanded", open);
};
document.addEventListener("click", (e) => { if (!$("accountMenu").contains(e.target)) $("accountPop").classList.add("hidden"); });
$("signOutBtn").onclick = async () => { await api("/api/auth/logout", { method: "POST" }).catch(() => {}); location.reload(); };
$("deleteAccountBtn").onclick = async () => {
  const err = $("deleteError");
  if (!isGuest && !$("deletePw").value) { err.textContent = "Enter your password to confirm."; err.classList.remove("hidden"); return; }
  if (!confirm(isGuest ? "Delete your guest practice permanently?" : "Delete your account and all your practice permanently?")) return;
  try {
    await api("/api/auth/account", { method: "DELETE", body: JSON.stringify({ password: $("deletePw").value }) });
    location.reload();
  } catch (e) { err.textContent = e.message; err.classList.remove("hidden"); }
};

export async function boot() {
  const [me, cfg] = await Promise.all([api("/api/auth/me"), api("/api/config").catch(() => ({}))]);
  guestsOn = !!cfg.guests;
  guestLimit = cfg.guest_take_limit || 3;
  $("authBeta").classList.toggle("hidden", !cfg.beta?.on);
  if (me.mode === "accounts" && !me.user) return showAuth("signup");
  showAccount(me);
  await init();
  resumeActiveJob();
}
