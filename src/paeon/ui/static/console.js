// Nothing here is needed to read the console: without this script the board is a page you
// reload, the filter box does nothing and the scheme follows the system.

// On a phone the navigation scrolls sideways: bring the current page into view.
document.querySelector(".nav [aria-current]")?.scrollIntoView({ inline: "center", block: "nearest" });

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

// The filter box: hides what does not match, on this page only. "/" goes to it.
const filter = (() => {
  const input = document.querySelector("[data-filter]");
  if (!input) return () => {};
  const scope = document.querySelector(input.dataset.filterIn) || document;
  const none = document.querySelector("[data-filter-none]");
  function apply() {
    const words = input.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    let shown = 0;
    for (const item of scope.querySelectorAll(input.dataset.filter)) {
      const text = item.textContent.toLowerCase();
      item.hidden = !words.every((word) => text.includes(word));
      if (!item.hidden) shown += 1;
    }
    for (const group of scope.querySelectorAll("[data-bay]")) {
      group.hidden = words.length > 0 && !group.querySelector(`${input.dataset.filter}:not([hidden])`);
    }
    if (none) none.hidden = words.length === 0 || shown > 0;
  }
  input.addEventListener("input", apply);
  input.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    input.value = "";
    apply();
    input.blur();
  });
  document.addEventListener("keydown", (event) => {
    const typing = event.target.closest?.("input, textarea, select, [contenteditable]");
    if (event.key !== "/" || typing || event.metaKey || event.ctrlKey || event.altKey) return;
    event.preventDefault();
    input.focus();
  });
  return apply;
})();

// The board refreshes itself. An incident that moved to another stage slides there, and one
// that changed keeps an "updated" tag until it is touched.
(() => {
  const board = document.querySelector("[data-live]");
  if (!board) return;

  const note = document.querySelector("[data-live-status]");
  const changed = new Set();
  const EVERY_MS = 10000;
  let shown = null;

  const items = () => [...board.querySelectorAll(".inc")];
  const state = () =>
    new Map(items().map((s) => [s.dataset.id, `${s.dataset.rev}@${s.closest("[data-bay]").dataset.bay}`]));

  function dress() {
    for (const item of items()) {
      // Set from script: the page's policy refuses inline styles.
      item.style.viewTransitionName = `inc-${item.dataset.id}`;
      if (changed.has(item.dataset.id) && !item.querySelector(".changed")) {
        item.classList.add("is-changed");
        const tag = document.createElement("span");
        tag.className = "tag changed";
        tag.textContent = "updated";
        item.querySelector(".inc-when").before(tag);
      }
    }
  }

  function seen(event) {
    const item = event.target.closest?.(".inc");
    if (!item) return;
    changed.delete(item.dataset.id);
    item.classList.remove("is-changed");
    item.querySelector(".changed")?.remove();
  }

  function say(text, stale) {
    if (!note) return;
    note.textContent = text;
    note.toggleAttribute("data-stale", stale);
  }

  async function refresh() {
    // Not while hidden, and not under someone's focus: replacing the board would drop it.
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
    say(`Live. Refreshed at ${at}.`, false);
    if (html === shown) return;
    const before = state();
    const swap = () => {
      // Parsed as a document, never as markup of this page: nothing in it can run.
      const parsed = new DOMParser().parseFromString(html, "text/html");
      board.replaceChildren(...parsed.body.childNodes);
      const first = shown === null;
      shown = html;
      for (const [id, now] of state()) {
        if (!first && before.get(id) !== now) changed.add(id);
      }
      dress();
      filter();
    };
    const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (document.startViewTransition && !calm) {
      // A transition is skipped when the tab goes to the background: the swap still happened.
      const moving = document.startViewTransition(swap);
      moving.ready.catch(() => {});
      moving.finished.catch(() => {});
    } else swap();
  }

  dress();
  board.addEventListener("pointerover", seen);
  board.addEventListener("pointerdown", seen);
  board.addEventListener("focusin", seen);
  setInterval(refresh, EVERY_MS);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh();
  });
})();
