// Rendering for one rolling quota window, shared by the chat overlay and the
// admin console so a limit reads the same in both places.
//
// Every value here comes from the server's own wire shape (amounts as decimal
// strings, so nothing is reshaped by float rounding). This module only
// displays: it never decides whether a turn is allowed.

const LABELS = { budget: "Monthly", burst: "Burst" };

export function money(amount) {
  const value = Number(amount);
  if (!Number.isFinite(value)) return "$0.00";
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  // Sub-dollar spend is the normal case for a single turn, so keep enough
  // digits for it to be visible rather than rounding it away to $0.00.
  const digits = abs === 0 || abs >= 1 ? 2 : 4;
  return `${sign}$${abs.toFixed(digits)}`;
}

export function windowName(name) {
  return LABELS[name] || name;
}

export function windowSpan(hours) {
  if (hours % 24 === 0 && hours >= 24) {
    const days = hours / 24;
    return days === 1 ? "24h" : `${days} days`;
  }
  return `${hours}h`;
}

/** Fraction of the limit used, or null when the window is unlimited. */
export function fraction(w) {
  if (w.limit_usd == null) return null;
  const limit = Number(w.limit_usd);
  if (!(limit > 0)) return Number(w.spent_usd) > 0 ? 1 : 0;
  return Number(w.spent_usd) / limit;
}

export function state(w) {
  const used = fraction(w);
  if (used == null) return "none";
  if (used >= 1) return "over";
  if (used >= 0.75) return "warn";
  return "ok";
}

/** "in 3h" / "in 12 min", or null when there is nothing to age out. */
export function until(iso) {
  if (!iso) return null;
  const ms = new Date(iso).getTime() - Date.now();
  if (!Number.isFinite(ms) || ms <= 0) return "any moment";
  const minutes = Math.round(ms / 60000);
  if (minutes < 60) return `in ${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `in ${hours}h`;
  return `in ${Math.round(hours / 24)} days`;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

export function track(w) {
  const bar = el("div", "quota-track");
  bar.dataset.state = state(w);
  const used = fraction(w);
  const fill = el("span", "quota-fill");
  if (used != null) fill.style.width = `${Math.min(100, Math.max(0, used * 100))}%`;
  bar.append(fill);
  return bar;
}

/** A labelled meter: name, spent-of-limit, bar, and when it frees up. */
export function meter(w) {
  const wrap = el("div", "meter");
  const head = el("div", "meter-head");
  const name = el("span", "meter-name", windowName(w.window));
  name.append(el("span", "meter-window", ` · ${windowSpan(w.window_hours)}`));
  const value =
    w.limit_usd == null
      ? `${money(w.spent_usd)} · no limit`
      : `${money(w.spent_usd)} of ${money(w.limit_usd)}`;
  head.append(name, el("span", "meter-value", value));
  wrap.append(head, track(w));

  const over = w.remaining_usd != null && Number(w.remaining_usd) <= 0;
  const frees = until(w.resets_at);
  if (over) {
    // Overrun is expected here: a turn already running is never cut off.
    wrap.append(
      el("span", "meter-note", frees ? `Exhausted · frees up ${frees}` : "Exhausted"),
    );
  } else if (w.remaining_usd != null) {
    const note = `${money(w.remaining_usd)} left`;
    wrap.append(el("span", "meter-note", frees ? `${note} · oldest spend ages out ${frees}` : note));
  }
  return wrap;
}

/** Replace ``host``'s children with one meter per window. */
export function meters(host, windows) {
  host.replaceChildren(...(windows || []).map(meter));
}

/** The window a user will hit first, for the compact topbar chip. */
export function tightest(windows) {
  const limited = (windows || []).filter((w) => w.limit_usd != null);
  if (!limited.length) return null;
  return limited.reduce((a, b) => (fraction(b) > fraction(a) ? b : a));
}
