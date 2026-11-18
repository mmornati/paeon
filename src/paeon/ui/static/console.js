// The board refreshes itself. A strip that moved to another bay slides there, and a strip that
// changed keeps a "changed" tag until it is touched. Without this script the board is a page
// you reload: nothing else depends on it.
(() => {
  const board = document.querySelector("[data-live]");
  if (!board) return;

  const note = document.querySelector("[data-live-status]");
  const changed = new Set();
  const EVERY_MS = 10000;
  let shown = null;

  const strips = () => [...board.querySelectorAll(".strip")];
  const state = () =>
    new Map(strips().map((s) => [s.dataset.id, `${s.dataset.rev}@${s.closest(".bay").dataset.bay}`]));

  function dress() {
    for (const strip of strips()) {
      // Set from script: the page's policy refuses inline styles.
      strip.style.viewTransitionName = `strip-${strip.dataset.id}`;
      if (changed.has(strip.dataset.id) && !strip.querySelector(".changed")) {
        strip.classList.add("is-changed");
        const tag = document.createElement("span");
        tag.className = "changed";
        tag.textContent = "changed";
        // In the line that says when: it never covers the title or a box.
        strip.querySelector(".standing-when").after(tag);
      }
    }
  }

  function seen(event) {
    const strip = event.target.closest?.(".strip");
    if (!strip) return;
    changed.delete(strip.dataset.id);
    strip.classList.remove("is-changed");
    strip.querySelector(".changed")?.remove();
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
    say(`Refreshed at ${at}.`, false);
    if (html === shown) return;
    const before = state();
    const swap = () => {
      // Parsed as a document, never as markup of this page: nothing in it can run.
      const parsed = new DOMParser().parseFromString(html, "text/html");
      board.replaceChildren(...parsed.body.childNodes);
      shown = html;
      for (const [id, now] of state()) {
        if (before.get(id) !== now) changed.add(id);
      }
      dress();
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
