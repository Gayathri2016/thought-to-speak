/* Thought → Speak — auth.js: Accounts: sign-in screen, account menu, sign-out and delete account, app boot. */
import { $ } from "./core.js";
import { api } from "./api.js";
import { init } from "./main.js";
import { resumeActiveJob } from "./recording.js";

/* ------------------------------------------------------------------ accounts */
export let authView = "signup";
export function showAuth(view = "signup", message = "") {
  authView = view;
  const up = view === "signup";
  document.body.classList.add("signed-out");
  $("authScreen").classList.remove("hidden");
  $("authTitle").textContent = up ? "Create your account" : "Welcome back";
  $("authSub").textContent = up ? "Practice out loud, get coached on content and voice, and track your progress."
    : "Sign in to pick up where you left off.";
  $("nameField").classList.toggle("hidden", !up);
  $("pwHint").classList.toggle("hidden", !up);
  $("authPassword").autocomplete = up ? "new-password" : "current-password";
  $("authSubmit").textContent = up ? "Create account" : "Sign in";
  $("authSwitchText").textContent = up ? "Already have an account?" : "New here?";
  $("authSwitch").textContent = up ? "Sign in" : "Create an account";
  $("authError").textContent = message;
  $("authError").classList.toggle("hidden", !message);
  setTimeout(() => $(up ? "authName" : "authEmail").focus(), 0);
}
$("authSwitch").onclick = (e) => { e.preventDefault(); showAuth(authView === "signup" ? "signin" : "signup"); };
$("authForm").onsubmit = async (e) => {
  e.preventDefault();
  const btn = $("authSubmit"); btn.disabled = true;
  const body = { email: $("authEmail").value, password: $("authPassword").value };
  if (authView === "signup") body.name = $("authName").value;
  try {
    const r = await api(`/api/auth/${authView === "signup" ? "signup" : "login"}`, { method: "POST", body: JSON.stringify(body) });
    $("authPassword").value = "";
    location.reload();          // start the app fresh as this user
    return r;
  } catch (err) {
    $("authError").textContent = err.message; $("authError").classList.remove("hidden");
  } finally { btn.disabled = false; }
};

export function showAccount(me) {
  if (me.mode !== "accounts" || !me.user) return;
  const u = me.user, label = (u.name || u.email).trim();
  $("accountMenu").classList.remove("hidden");
  $("dangerZone").classList.remove("hidden");
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
  if (!$("deletePw").value) { err.textContent = "Enter your password to confirm."; err.classList.remove("hidden"); return; }
  if (!confirm("Delete your account and all your practice permanently?")) return;
  try {
    await api("/api/auth/account", { method: "DELETE", body: JSON.stringify({ password: $("deletePw").value }) });
    location.reload();
  } catch (e) { err.textContent = e.message; err.classList.remove("hidden"); }
};

export async function boot() {
  const me = await api("/api/auth/me");
  if (me.mode === "accounts" && !me.user) return showAuth("signup");
  showAccount(me);
  await init();
  resumeActiveJob();
}
