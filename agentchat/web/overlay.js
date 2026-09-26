// Product overlay on LingChat's AgentGUI page.
//
// Loaded before the app module so it can wrap WebSocket and observe frames the
// upstream page does not know about (quota refusals, the charge on a usage
// frame). Everything here is presentation: the server owns quota decisions and
// resolves workspace names to paths, so nothing in this file can raise a limit
// or reach another user's files.

const $ = (id) => document.getElementById(id);

// --- quota chip --------------------------------------------------------------

function renderQuota(windows) {
  if (!Array.isArray(windows) || !windows.length) return;
  const chip = $("quota-chip");
  if (!chip) return;
  const parts = [];
  for (const w of windows) {
    if (w.limit_usd == null) continue;
    const remaining = Number(w.remaining_usd);
    parts.push(`${w.window} $${remaining.toFixed(2)} left`);
    if (remaining <= 0) chip.dataset.state = "empty";
  }
  chip.textContent = parts.join(" · ") || "unlimited";
  chip.title = windows
    .map((w) => `${w.window}: $${Number(w.spent_usd).toFixed(4)} spent in ${w.window_hours}h`)
    .join("\n");
  chip.hidden = false;
}

async function refreshQuota() {
  try {
    const response = await fetch("/api/me/quota");
    if (!response.ok) return;
    const data = await response.json();
    renderQuota(data.windows);
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
  const note = document.createElement("div");
  note.className = `note ${kind}`;
  note.textContent = text;
  row.append(note);
  thread.append(row);
  row.scrollIntoView({ block: "end" });
}

function onFrame(msg) {
  if (!msg || typeof msg !== "object") return;
  if (msg.type === "quota_exceeded") {
    const retry = msg.retry_at ? ` Try again after ${new Date(msg.retry_at).toLocaleString()}.` : "";
    note("error", `${msg.message || "Quota exhausted."}${retry}`);
    const chip = $("quota-chip");
    if (chip) chip.dataset.state = "empty";
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

function mountTopbar(user) {
  const meta = document.querySelector(".topbar-meta");
  if (!meta) return;
  const quota = document.createElement("span");
  quota.id = "quota-chip";
  quota.className = "agent-chip";
  quota.hidden = true;
  const who = document.createElement("span");
  who.className = "agent-chip";
  who.textContent = user.role === "admin" ? `${user.username} (admin)` : user.username;
  const out = document.createElement("button");
  out.className = "btn";
  out.textContent = "Sign out";
  out.addEventListener("click", async () => {
    await fetch("/api/auth/logout", { method: "POST" });
    location.href = "/login";
  });
  meta.prepend(quota, who, out);
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
