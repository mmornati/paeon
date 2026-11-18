// Paeon's site. Without this script the page reads the same: the board shows the poll holding
// on YOU, the dial shows its default level, and every section is plain HTML.
document.documentElement.classList.add("js");

// --- the poll: stations report in turn, then the board holds on YOU ----------------------
(() => {
  const board = document.querySelector("[data-poll]");
  if (!board) return;
  const rows = [...board.querySelectorAll(".row")];
  const before = rows.filter((r) => !["you", "executor"].includes(r.dataset.station));
  const you = board.querySelector('[data-station="you"]');
  const exec = board.querySelector('[data-station="executor"]');
  const youLamp = board.querySelector("[data-you-lamp]");
  const youNote = board.querySelector("[data-you-note]");
  const execLamp = board.querySelector("[data-exec-lamp]");
  const execNote = board.querySelector("[data-exec-note]");
  const guard = board.querySelector("[data-guard]");
  const lift = board.querySelector("[data-lift]");
  const go = board.querySelector("[data-go]");
  const nogo = board.querySelector("[data-nogo]");
  const say = board.querySelector("[data-say]");
  const again = board.querySelector("[data-again]");
  const clock = board.querySelector("[data-clock]");
  const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let timers = [];
  let started = 0;
  let ticking = null;

  const later = (ms, fn) => timers.push(setTimeout(fn, calm ? 0 : ms));
  const lamp = (el, state, label) => { el.dataset.state = state; el.textContent = label; };
  const tick = () => {
    const s = Math.floor((Date.now() - started) / 1000);
    clock.textContent = `T+${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  };

  function reset() {
    timers.forEach(clearTimeout); timers = [];
    clearInterval(ticking);
    started = Date.now(); tick(); ticking = setInterval(tick, 1000);
    guard.classList.remove("is-open", "is-spent");
    lift.setAttribute("aria-expanded", "false"); lift.disabled = false;
    go.disabled = nogo.disabled = true;
    again.hidden = true;
    lamp(youLamp, "hold", "Hold"); youNote.textContent = "waiting for your answer";
    lamp(execLamp, "standby", "Standby"); execNote.textContent = "nothing runs without Go";
    say.textContent = "Stations are reporting.";
    [...before, you, exec].forEach((r) => r.classList.add("is-waiting"));
    before.forEach((r, i) => later(500 + i * 620, () => r.classList.remove("is-waiting")));
    later(500 + before.length * 620, () => {
      you.classList.remove("is-waiting"); exec.classList.remove("is-waiting");
      say.textContent = "The poll holds on you. Lift the guard, then answer.";
    });
  }

  lift.addEventListener("click", () => {
    guard.classList.add("is-open");
    lift.setAttribute("aria-expanded", "true"); lift.disabled = true;
    go.disabled = nogo.disabled = false;
    say.textContent = "Lifting the guard applied nothing. Go or No-go?";
    go.focus({ preventScroll: true });
  });

  function answered() {
    go.disabled = nogo.disabled = true;
    guard.classList.add("is-spent");
    clearInterval(ticking);
  }

  go.addEventListener("click", () => {
    answered();
    lamp(youLamp, "go", "Go"); youNote.textContent = "you said Go";
    lamp(execLamp, "run", "Pre-check"); execNote.textContent = "is the world still as the plan assumed?";
    say.textContent = "The button worked once. A second tap would do nothing.";
    later(1300, () => { lamp(execLamp, "run", "Apply"); execNote.textContent = "restart_container(media-server)"; });
    later(2900, () => { lamp(execLamp, "run", "Verify"); execNote.textContent = "waiting for running and healthy"; });
    later(4700, () => {
      lamp(execLamp, "go", "Go"); execNote.textContent = "restarted, verified healthy";
      say.textContent = "Fix applied. Your phone says so, with a button to say it was wrong.";
      again.hidden = false;
    });
  });

  nogo.addEventListener("click", () => {
    answered();
    lamp(youLamp, "nogo", "No-go"); youNote.textContent = "you said No-go";
    lamp(execLamp, "standby", "Stood down"); execNote.textContent = "nothing ran";
    say.textContent = "Nothing ran. The link is dead, and the incident is yours.";
    again.hidden = false;
  });

  again.addEventListener("click", reset);
  reset();
})();

// --- the dial: what each level does -----------------------------------------------------
(() => {
  const dial = document.querySelector("[data-dial]");
  if (!dial) return;
  const LEVELS = {
    observe: {
      title: "Observe", runs: "nothing",
      text: "Paeon records what would have been done, and tells nobody. It is where the kill switch drops everything.",
      notif: null,
    },
    suggest: {
      title: "Suggest", runs: "nothing",
      text: "Paeon tells you what it would do, with no button. The plan is in the analysis; running it is up to you.",
      notif: { title: "Incident #3: slo_traefik_5xx", body: "Suggested: restart the container (media-server). No button: this one is yours." },
    },
    approve: {
      title: "Approve", runs: "the plan, after your Go",
      text: "The executor asks you. The button is signed, works once, for thirty minutes, for that exact plan. No answer: nothing happens, and one reminder says so.",
      notif: { title: "Incident #3: run this fix?", body: "1. Restart the container (media-server). Nothing runs without Go.", keys: [["Go", "go"], ["No-go", "no"]] },
    },
    auto: {
      title: "Auto", runs: "the plan, then it tells you",
      text: "The executor runs it, verifies it, then tells you, always with “This was wrong” and a kill switch. Never on an incident with several alert classes, under the confidence threshold, or twice on one incident.",
      notif: { title: "Incident #3: fix applied", body: "Ran without asking (auto): restart the container (media-server). Verified: up and healthy.", keys: [["This was wrong", "plain"], ["Stop automation", "plain"]] },
    },
  };
  const text = dial.querySelector("[data-dial-text]");
  const notif = dial.querySelector("[data-dial-notif]");
  const keys = [...dial.querySelectorAll("[data-level]")];
  const el = (tag, cls, content) => { const e = document.createElement(tag); if (cls) e.className = cls; if (content) e.textContent = content; return e; };

  function show(name) {
    const l = LEVELS[name];
    keys.forEach((k) => { const on = k.dataset.level === name; k.setAttribute("aria-checked", String(on)); k.tabIndex = on ? 0 : -1; });
    const ran = el("p", "dial-ran", "What runs: ");
    ran.append(el("strong", "", l.runs));
    text.replaceChildren(el("h3", "", l.title), el("p", "", l.text), ran);
    const app = el("p", "notif-app");
    app.append(el("span", "mark-lamp"), "Paeon", el("span", "", "now"));
    if (!l.notif) {
      notif.replaceChildren(app, el("p", "notif-body", "No notification. The plan is a line in the event log."));
      return;
    }
    const kids = [app, el("p", "notif-title", l.notif.title), el("p", "notif-body", l.notif.body)];
    if (l.notif.keys) {
      const row = el("p", "notif-keys");
      l.notif.keys.forEach(([label, kind]) => row.append(el("span", `k-${kind}`, label)));
      kids.push(row);
    }
    notif.replaceChildren(...kids);
  }

  keys.forEach((k, i) => {
    k.addEventListener("click", () => show(k.dataset.level));
    k.addEventListener("keydown", (e) => {
      const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
      if (!step) return;
      e.preventDefault();
      const next = keys[(i + step + keys.length) % keys.length];
      next.focus(); show(next.dataset.level);
    });
  });
  show("suggest");
})();

// --- the reel and the monitor: one screen, several things to put on it -------------------
function tabs(root, attr, pick) {
  if (!root) return;
  const all = [...root.querySelectorAll(`[${attr}]`)];
  all.forEach((tab) => tab.addEventListener("click", () => {
    all.forEach((t) => t.setAttribute("aria-selected", String(t === tab)));
    pick(tab);
  }));
}
tabs(document.querySelector("[data-theatre]"), "data-video", (tab) => {
  const screen = document.querySelector("[data-screen]");
  screen.src = `videos/${tab.dataset.video}/?embed=1&lang=en`;
  screen.title = tab.dataset.title;
});
tabs(document.querySelector("[data-monitor]"), "data-shot", (tab) => {
  const img = document.querySelector("[data-monitor-img]");
  img.src = `docs/assets/console/${tab.dataset.shot}.webp`;
  img.alt = tab.dataset.alt;
});

// --- copy buttons on every command ------------------------------------------------------
document.querySelectorAll("pre").forEach((pre) => {
  if (!navigator.clipboard) return;
  const wrap = document.createElement("div");
  wrap.className = "cmd";
  pre.replaceWith(wrap);
  const button = document.createElement("button");
  button.className = "copy"; button.type = "button"; button.textContent = "Copy";
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(pre.querySelector("code").textContent);
      button.textContent = "Copied";
    } catch {
      button.textContent = "Not copied";
    }
    setTimeout(() => { button.textContent = "Copy"; }, 1600);
  });
  wrap.append(pre, button);
});
