// Nothing here is needed to read the console: without this script the incidents are a page you
// reload, every filter is a link and the scheme follows the system.

// The "More" menu of the phone's tab bar closes on Escape and on a tap elsewhere.
(() => {
  const more = document.querySelector(".tab-more");
  if (!more) return;
  document.addEventListener("click", (event) => {
    if (more.open && !more.contains(event.target)) more.open = false;
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && more.open) {
      more.open = false;
      more.querySelector("summary").focus();
    }
  });
})();

// Light or dark, kept for the next visit.
(() => {
  const button = document.querySelector("[data-theme-toggle]");
  if (!button) return;
  button.hidden = false;
  const dark = () =>
    (document.documentElement.dataset.theme ||
      (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")) === "dark";
  const tell = () => button.setAttribute("aria-pressed", String(dark()));
  tell();
  button.addEventListener("click", () => {
    const next = dark() ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("paeon-theme", next);
    } catch {
      // Not kept: the choice still holds for this page.
    }
    tell();
  });
})();

// The explorer's search box: "/" goes to it, Escape clears it and shows everything again.
(() => {
  const input = document.querySelector("[data-search]");
  if (!input) return;
  input.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (input.value) {
      input.value = "";
      input.form.requestSubmit();
    } else input.blur();
  });
  document.addEventListener("keydown", (event) => {
    const typing = event.target.closest?.("input, textarea, select, [contenteditable]");
    if (event.key !== "/" || typing || event.metaKey || event.ctrlKey || event.altKey) return;
    event.preventDefault();
    input.focus();
    input.select();
  });
})();

// j and k move through the incidents, Enter opens one.
(() => {
  if (!document.querySelector("[data-live]")) return;
  document.addEventListener("keydown", (event) => {
    const typing = event.target.closest?.("input, textarea, select, [contenteditable]");
    if (typing || event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.key !== "j" && event.key !== "k") return;
    const links = [...document.querySelectorAll(".ex-title")];
    if (!links.length) return;
    const at = links.indexOf(document.activeElement);
    const next = event.key === "j" ? Math.min(at + 1, links.length - 1) : Math.max(at - 1, 0);
    event.preventDefault();
    links[at === -1 ? 0 : next].focus();
    links[at === -1 ? 0 : next].closest("tr")?.scrollIntoView({ block: "nearest" });
  });
})();

// On a phone the filters start folded: the rows come first.
function foldFilters(root) {
  if (matchMedia("(min-width: 60rem)").matches) return;
  for (const details of root.querySelectorAll("[data-facets]")) {
    if (!details.dataset.touched) details.open = false;
  }
}
foldFilters(document);
document.addEventListener(
  "toggle",
  (event) => {
    if (event.target.matches?.("[data-facets]")) event.target.dataset.touched = "1";
  },
  true,
);

// The explorer refreshes itself with the same filters. A row that changed is marked until it
// is touched; the status line says when it last refreshed, without announcing it each time.
(() => {
  const board = document.querySelector("[data-live]");
  if (!board) return;

  const note = document.querySelector("[data-live-status]");
  const changed = new Set();
  const EVERY_MS = 10000;
  let shown = null;

  const rows = () => [...board.querySelectorAll(".ex-row")];
  const state = () => new Map(rows().map((r) => [r.dataset.id, r.dataset.rev]));

  function dress() {
    for (const row of rows()) {
      if (!changed.has(row.dataset.id) || row.querySelector(".changed")) continue;
      row.classList.add("is-changed");
      const tag = document.createElement("span");
      tag.className = "tag changed";
      tag.textContent = "updated";
      row.querySelector(".c-title").append(" ", tag);
    }
  }

  function seen(event) {
    const row = event.target.closest?.(".ex-row");
    if (!row) return;
    changed.delete(row.dataset.id);
    row.classList.remove("is-changed");
    row.querySelector(".changed")?.remove();
  }

  function say(text, stale) {
    if (!note) return;
    note.textContent = text;
    note.toggleAttribute("data-stale", stale);
    // Only trouble is worth interrupting a screen reader for.
    if (stale) note.setAttribute("role", "alert");
    else note.removeAttribute("role");
  }

  async function refresh() {
    // Not while hidden, and not under someone's focus: replacing the rows would drop it.
    if (document.hidden || board.contains(document.activeElement)) return;
    let html;
    try {
      const response = await fetch(board.dataset.live, { headers: { Accept: "text/html" } });
      if (!response.ok) throw new Error(String(response.status));
      html = await response.text();
    } catch {
      say("Not refreshing: Paeon did not answer. Reload to try again.", true);
      return;
    }
    const at = new Date().toLocaleTimeString([], { hour12: false });
    say(`Live, ${at}`, false);
    if (html === shown) return;
    const before = state();
    const filters = board.querySelector("[data-facets]");
    const keep = filters?.dataset.touched ? filters.open : null;
    // Parsed as a document, never as markup of this page: nothing in it can run.
    const parsed = new DOMParser().parseFromString(html, "text/html");
    board.replaceChildren(...parsed.body.childNodes);
    const swapped = board.querySelector("[data-facets]");
    if (swapped && keep !== null) {
      swapped.open = keep;
      swapped.dataset.touched = "1";
    } else foldFilters(board);
    const first = shown === null;
    shown = html;
    for (const [id, rev] of state()) {
      if (!first && before.has(id) && before.get(id) !== rev) changed.add(id);
      if (!first && !before.has(id)) changed.add(id);
    }
    dress();
  }

  board.addEventListener("pointerover", seen);
  board.addEventListener("pointerdown", seen);
  board.addEventListener("focusin", seen);
  setInterval(refresh, EVERY_MS);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh();
  });
})();
