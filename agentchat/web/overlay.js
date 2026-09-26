// Product overlay on LingChat's AgentGUI page.
//
// Loaded before the app module so it can wrap WebSocket and observe frames the
// upstream page does not know about (quota refusals, the charge on a usage
// frame). Everything here is presentation: the server owns quota decisions and
// resolves workspace names to paths, so nothing in this file can raise a limit
// or reach another user's files.

import { meters, money, tightest, track, windowName } from "./quota.js";

const $ = (id) => document.getElementById(id);

// --- quota chip + panel ------------------------------------------------------

function renderQuota(windows) {
  if (!Array.isArray(windows) || !windows.length) return;
  const chip = $("quota-chip");
  const panel = $("quota-meters");
  if (panel) meters(panel, windows);
  if (!chip) return;

  const tight = tightest(windows);
  chip.replaceChildren();
  if (!tight) {
    chip.append(document.createTextNode("No quota limit"));
  } else {
    // The chip shows the window that will stop the next turn first; the panel
    // has the full picture.
    const remaining = Number(tight.remaining_usd);
    chip.append(
      track(tight),
      document.createTextNode(
        remaining <= 0
          ? `${windowName(tight.window)} spent`
          : `${money(tight.remaining_usd)} left`,
      ),
    );
  }
  chip.hidden = false;
}

async function refreshQuota() {
  try {
    const response = await fetch("/api/me/quota");
    if (!response.ok) return;
    renderQuota((await response.json()).windows);
  } catch {
    /* transient: the chip simply keeps its last value */
  }
}

// --- frames the upstream page ignores ---------------------------------------

function note(kind, text) {
  const thread = $("thread");
  if (!thread || !text) return;
  // Same classes the page styles its own notes with.
  const row = document.createElement("div");
  row.className = "row event";
  const body = document.createElement("div");
  body.className = `note ${kind}`;
  body.textContent = text;
  row.append(body);
  thread.append(row);
  row.scrollIntoView({ block: "end" });
}

function onFrame(msg) {
  if (!msg || typeof msg !== "object") return;
  if (msg.type === "quota_exceeded") {
    const retry = msg.retry_at
      ? ` Try again after ${new Date(msg.retry_at).toLocaleString()}.`
      : "";
    note("error", `${msg.message || "Quota exhausted."}${retry}`);
    openPanel(true);
    refreshQuota();
  } else if (msg.type === "usage") {
    if (Array.isArray(msg.quota)) renderQuota(msg.quota);
    if (msg.billing_error) note("error", `Usage was not recorded: ${msg.billing_error}`);
  }
}

// Wrap the socket instead of patching the app's modules: read-only observation
// that survives their reconnects and never alters what they receive.
const NativeWebSocket = window.WebSocket;
window.WebSocket = function PatchedWebSocket(...args) {
  const socket = new NativeWebSocket(...args);
  socket.addEventListener("message", (event) => {
    try {
      onFrame(JSON.parse(event.data));
    } catch {
      /* not JSON, or a shape we do not read */
    }
  });
  return socket;
};
window.WebSocket.prototype = NativeWebSocket.prototype;
Object.assign(window.WebSocket, NativeWebSocket);

// --- chrome: who is signed in, sign out, workspace naming -------------------

function openPanel(open) {
  const chip = $("quota-chip");
  const panel = $("quota-panel");
  if (!chip || !panel) return;
  panel.hidden = !open;
  chip.setAttribute("aria-expanded", String(open));
}

function mountQuota(meta) {
  const wrap = document.createElement("span");
  wrap.className = "quota-wrap";
  const chip = document.createElement("button");
  chip.id = "quota-chip";
  chip.className = "quota-chip";
  chip.type = "button";
  chip.hidden = true;
  chip.setAttribute("aria-expanded", "false");
  chip.setAttribute("aria-label", "Usage quota");

  const panel = document.createElement("div");
  panel.id = "quota-panel";
  panel.className = "quota-panel";
  panel.hidden = true;
  panel.setAttribute("role", "group");
  panel.setAttribute("aria-label", "Usage quota");
  const heading = document.createElement("h3");
  heading.textContent = "Usage quota";
  const body = document.createElement("div");
  body.id = "quota-meters";
  body.style.display = "grid";
  body.style.gap = "14px";
  const foot = document.createElement("p");
  foot.className = "quota-foot";
  foot.textContent =
    "Both limits roll: spend leaves a window as it ages out, so a refusal lifts by itself. A turn already running is never cut off.";
  panel.append(heading, body, foot);
  wrap.append(chip, panel);

  chip.addEventListener("click", () => {
    const opening = panel.hidden;
    openPanel(opening);
    if (opening) refreshQuota();
  });
  document.addEventListener("click", (event) => {
    if (!panel.hidden && !wrap.contains(event.target)) openPanel(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !panel.hidden) {
      openPanel(false);
      chip.focus();
    }
  });
  meta.prepend(wrap);
}

function mountTopbar(user) {
  const meta = document.querySelector(".topbar-meta");
  if (!meta) return;
  const who = document.createElement("span");
  who.className = "agent-chip";
  who.textContent = user.username;
  const out = document.createElement("button");
  out.className = "btn btn-sm";
  out.textContent = "Sign out";
  out.addEventListener("click", async () => {
    await fetch("/api/auth/logout", { method: "POST" });
    location.href = "/login";
  });
  if (user.role === "admin") {
    const admin = document.createElement("a");
    admin.className = "btn btn-sm";
    admin.href = "/admin";
    admin.textContent = "Admin";
    meta.prepend(admin);
  }
  meta.prepend(who, out);
  mountQuota(meta);
}

function relabelWorkspace() {
  // The server resolves a name under this user's own root, so the field is a
  // label rather than a filesystem path.
  const label = document.querySelector('label[for="new-workspace"]');
  if (label) label.textContent = "Workspace name";
  const input = $("new-workspace");
  if (input) {
    input.placeholder = "default";
    input.pattern = "[A-Za-z0-9][A-Za-z0-9._-]*";
    input.title = "Letters, digits, dot, dash and underscore";
  }
}

(async function start() {
  const response = await fetch("/api/me");
  if (response.status === 401) {
    location.href = "/login";
    return;
  }
  const user = await response.json();
  mountTopbar(user);
  relabelWorkspace();
  // Installation checks report host paths, so they are an admin tool.
  if (user.role !== "admin") $("doctor-open")?.remove();
  refreshQuota();
})();
