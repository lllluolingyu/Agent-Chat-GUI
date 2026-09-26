// Admin console: accounts, quota limits, and the charge ledger.
//
// Every action here is a call to an admin-only API that re-checks the caller's
// role server-side; hiding or showing a control in this file is a convenience,
// never the access decision.

import { meter, money } from "./quota.js";

const $ = (id) => document.getElementById(id);

function say(id, text, kind = "error") {
  const node = $(id);
  if (!node) return;
  node.textContent = text || "";
  node.dataset.kind = kind;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: options.body ? { "content-type": "application/json" } : {},
    ...options,
  });
  if (response.status === 401) {
    location.href = "/login";
    throw new Error("signed out");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(typeof data.detail === "string" ? data.detail : response.statusText);
  }
  return data;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function cell(row, className) {
  const td = el("td", className);
  row.append(td);
  return td;
}

// --- people -----------------------------------------------------------------

function roleSelect(user) {
  const select = el("select");
  for (const role of ["member", "admin"]) {
    const option = el("option", null, role);
    option.value = role;
    option.selected = user.role === role;
    select.append(option);
  }
  select.addEventListener("change", async () => {
    try {
      await api(`/api/admin/users/${user.id}`, {
        method: "PATCH",
        body: JSON.stringify({ role: select.value }),
      });
      await loadUsers();
    } catch (error) {
      say("users-note", error.message);
      select.value = user.role;
    }
  });
  return select;
}

function action(label, handler) {
  const button = el("button", "btn btn-sm", label);
  button.type = "button";
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await handler();
    } catch (error) {
      say("users-note", error.message);
    } finally {
      button.disabled = false;
    }
  });
  return button;
}

function userRow(user) {
  const row = el("tr");
  const who = cell(row);
  who.append(el("div", null, user.username));
  if (!user.active) {
    const tag = el("span", "tag", "disabled");
    tag.dataset.kind = "disabled";
    who.append(tag);
  }

  const role = cell(row);
  role.append(roleSelect(user));

  const quota = cell(row);
  const windows = user.quota?.windows || [];
  if (windows.length) {
    const stack = el("div");
    stack.style.display = "grid";
    stack.style.gap = "10px";
    stack.append(...windows.map(meter));
    quota.append(stack);
  } else {
    quota.append(el("span", "empty", "no limits"));
  }

  const actions = cell(row);
  const group = el("div", "row-actions");
  group.append(
    action("Quota…", () => openQuota(user)),
    action("Credit…", async () => {
      // A credit is negative spend, so it ages out with the window it lands in.
      const amount = prompt(`Credit ${user.username} how many USD?`, "5");
      if (!amount) return;
      await api(`/api/admin/users/${user.id}/credit`, {
        method: "POST",
        body: JSON.stringify({ amount_usd: amount.trim(), note: "admin credit" }),
      });
      say("users-note", `Credited ${money(amount)} to ${user.username}.`, "ok");
      await loadUsers();
    }),
    action("Password…", async () => {
      const password = prompt(`New password for ${user.username} (min 8 characters)`);
      if (!password) return;
      await api(`/api/admin/users/${user.id}`, {
        method: "PATCH",
        body: JSON.stringify({ password }),
      });
      // Every existing sign-in for that account is dropped server-side.
      say("users-note", `Password set for ${user.username}; they must sign in again.`, "ok");
    }),
    action(user.active ? "Disable" : "Enable", async () => {
      await api(`/api/admin/users/${user.id}`, {
        method: "PATCH",
        body: JSON.stringify({ active: !user.active }),
      });
      await loadUsers();
    }),
  );
  actions.append(group);
  return row;
}

async function loadUsers() {
  const users = (await api("/api/admin/users")).users;
  $("users").replaceChildren(...users.map(userRow));
  const picker = $("ledger-user");
  const chosen = picker.value;
  const everyone = el("option", null, "everyone");
  // Without an explicit empty value the option's value is its own text, so
  // restoring the "" selection below matched nothing and the filter read blank.
  everyone.value = "";
  picker.replaceChildren(everyone);
  for (const user of users) {
    const option = el("option", null, user.username);
    option.value = user.id;
    picker.append(option);
  }
  picker.value = chosen;
}

// --- quota dialog -----------------------------------------------------------

let editing = null;

function openQuota(user) {
  editing = user;
  const form = $("quota-form");
  const policy = user.quota?.policy || {};
  form.budget_usd.value = policy.budget_usd ?? "";
  form.burst_usd.value = policy.burst_usd ?? "";
  form.budget_window_hours.value = policy.budget_window_hours ?? 720;
  form.burst_window_hours.value = policy.burst_window_hours ?? 5;
  $("quota-who").textContent = user.username;
  say("quota-note", "");
  $("quota-dialog").showModal();
}

async function saveQuota() {
  const form = $("quota-form");
  try {
    await api(`/api/admin/users/${editing.id}/quota`, {
      method: "PUT",
      body: JSON.stringify({
        budget_usd: form.budget_usd.value.trim() || null,
        burst_usd: form.burst_usd.value.trim() || null,
        budget_window_hours: Number(form.budget_window_hours.value),
        burst_window_hours: Number(form.burst_window_hours.value),
      }),
    });
  } catch (error) {
    say("quota-note", error.message);
    return;
  }
  $("quota-dialog").close();
  await loadUsers();
  say("users-note", `Quota saved for ${editing.username}.`, "ok");
}

// --- ledger -----------------------------------------------------------------

async function loadLedger() {
  const user = $("ledger-user").value;
  const query = user ? `?limit=50&user_id=${encodeURIComponent(user)}` : "?limit=50";
  const data = await api(`/api/admin/usage${query}`);
  const rows = data.entries.map((entry) => {
    const row = el("tr");
    cell(row).textContent = new Date(entry.at).toLocaleString();
    cell(row).textContent = entry.username;
    const model = cell(row);
    model.append(el("div", null, entry.model));
    if (entry.pricing === "fallback") {
      const tag = el("span", "tag", "fallback");
      tag.dataset.kind = "fallback";
      tag.title = "Billed at the fallback rate: this model is not listed in prices.toml";
      model.append(tag);
    }
    cell(row, "num").textContent = entry.input.toLocaleString();
    cell(row, "num").textContent = entry.output.toLocaleString();
    cell(row, "num").textContent = money(entry.cost_usd);
    return row;
  });
  $("ledger").replaceChildren(
    ...(rows.length ? rows : [(() => {
      const row = el("tr");
      const only = cell(row, "empty");
      only.colSpan = 6;
      only.textContent = "No charges recorded yet.";
      return row;
    })()]),
  );
}

// --- startup ----------------------------------------------------------------

(async function start() {
  const me = await api("/api/me");
  if (me.role !== "admin") {
    location.href = "/";
    return;
  }
  $("whoami").textContent = `${me.username} (admin)`;
  $("sign-out").addEventListener("click", async () => {
    await fetch("/api/auth/logout", { method: "POST" });
    location.href = "/login";
  });
  $("quota-save").addEventListener("click", saveQuota);
  $("quota-dialog").querySelector("[data-close]").addEventListener("click", () =>
    $("quota-dialog").close(),
  );
  $("ledger-user").addEventListener("change", () => loadLedger().catch(() => {}));

  $("add-user").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.target);
    say("add-note", "");
    try {
      await api("/api/admin/users", {
        method: "POST",
        body: JSON.stringify({
          username: String(form.get("username")).trim(),
          password: form.get("password"),
          role: form.get("role"),
        }),
      });
    } catch (error) {
      say("add-note", error.message);
      return;
    }
    event.target.reset();
    say("add-note", "User created.", "ok");
    await loadUsers();
  });

  try {
    $("prices-path").textContent = (await api("/api/admin/prices")).path;
  } catch {
    /* the path is a convenience; the page works without it */
  }
  await loadUsers();
  await loadLedger();
})();
