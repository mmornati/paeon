/* Your hands on it: what 0.5 adds. An analysis that says what becomes of its plan, Go in the
 * console, re-check or close an incident, review a postmortem. Scenes are pure functions of t,
 * hooked onto the narration's sentences with at(cues, i). The look comes from ../kit.js. Every
 * incident, name and line here is fictional; every rule comes from docs/console.md (0.5.0). */
"use strict";

const STR = {
  en: {
    url: "mmornati.github.io/paeon",
    status: "PAEON 0.5.0 · GO IN THE CONSOLE IS OFF UNTIL YOU TURN IT ON",
  },
};

const within = (cues, i, f, s, e) => lerp(at(cues, i, s), atEnd(cues, i, e), f);
const label = (s, x, y, k, color = C.faint, o = {}) =>
  text(s, x, y, { size: 15, weight: 600, font: COND, color, spacing: "3px", alpha: k, ...o });

/* A browser window around a screenshot of the console. Returns the height drawn. */
function browser(x, y, w, name, k, o = {}) {
  if (k <= 0) return 0;
  panel(x, y, w, 38, { r: 6, fill: C.panel2, stroke: C.line, alpha: k });
  [C.bad, C.warn, C.good].forEach((c, i) => dot(x + 16 + i * 14, y + 15, 4.5, c, k * 0.8));
  text(o.url ?? "paeon.example.org/ui", x + w / 2, y + 20, { size: 12, font: MONO, color: C.faint, align: "center", alpha: k });
  return 30 + K.shot(o.name ?? name, x, y + 30, w, { k, crop: o.crop, shadow: o.shadow });
}

/* A plain panel of monospace lines: a rules file or the end of a message. */
function sheet(x, y, w, title, lines, k, o = {}) {
  if (k <= 0) return;
  const lh = o.lh ?? 28, h = 58 + lines.length * lh;
  panel(x, y, w, h, { r: 4, fill: C.deep, stroke: o.stroke ?? C.line, alpha: k });
  label(title, x + 20, y + 30, k, o.titleColor ?? C.faint);
  line([[x, y + 42], [x + w, y + 42]], C.line, 1, k);
  lines.forEach(([s, color, kk], i) =>
    text(typed(s, kk ?? 1), x + 22, y + 74 + i * lh, { size: o.size ?? 16, font: MONO, color: color ?? C.termInk, alpha: k }));
}

function sRan(t, d, cues) {
  K.head("INCIDENT #3", "A PLAN, AND THEN NOTHING", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.8), a1 = at(cues, 1, 4.2);
  K.phone(860, 120, 300, 470, {
    k: seg(t, a0 + 0.3, a0 + 0.9), clock: "21:08",
    title: "Incident #3: slo_traefik_5xx",
    lines: ["Root cause (81% sure): the disk", "of media-server is full.", "Suggested: restart_container", "    (media-server)"],
    buttons: [{ label: "CAUSE RIGHT", kind: "go" }, { label: "WRONG", kind: "no" }],
  }, seg(t, 0.3, 0.9));
  K.board(80, 190, 700, "INCIDENT #3 · WHAT HAPPENED NEXT", [
    { name: "ANALYSIS", note: "sent to your phone", voice: "code", state: "GO", label: "SENT", ks: seg(t, a0 + 1.0, a0 + 1.3), kn: seg(t, a0 + 0.8, a0 + 1.2), nx: 200 },
    { name: "PLAN", note: "handed to the executor", voice: "code", state: "GO", label: "SENT", ks: seg(t, a0 + 1.6, a0 + 1.9), kn: seg(t, a0 + 1.4, a0 + 1.8), nx: 200 },
    { name: "YOUR GO", note: "never asked", voice: "you", state: "", label: "", ks: 0, kn: seg(t, a1, a1 + 0.4), nx: 200 },
  ], seg(t, 0.4, 1.0));
  const kq = seg(t, a1 + 0.6, a1 + 1.2);
  text("WHY?", 80, 520 + (1 - outCubic(kq)) * 14, { size: 88, weight: 700, font: COND, spacing: "2px", color: C.accent, alpha: kq });
}

function sFate(t, d, cues) {
  K.head("THE LAST LINES OF AN ANALYSIS", "WHAT BECOMES OF THE PLAN", seg(t, 0.2, 0.9));
  const a1 = at(cues, 1, 4.2), a2 = at(cues, 2, 8.4);
  const f = i => within(cues, 1, i, a1 + i * 3, 0);
  const row = (name, note, state, lbl, s) => ({ name, note, voice: "code", state, label: lbl, ks: seg(t, s, s + 0.3), kn: seg(t, s - 0.2, s + 0.3), nx: 220, size: 17, h: 58 });
  K.board(80, 170, 1120, "WHAT PAEON DOES WITH IT", [
    row("WAITING", "for your Go: it comes in its own message, with Go and No-go", "HOLD", "YOUR GO", f(0.02)),
    row("APPROVED", "by the policies: it runs now, then you get the result", "GO", "RUNS", f(0.32)),
    row("RECORDED", "only: nothing will run and nothing will be asked", "", "", f(0.62)),
  ], seg(t, 0.4, 1.0));
  const kr = seg(t, f(0.62), f(0.62) + 0.4);
  if (kr > 0) K.lamp(1200 - 142, 170 + 44 + 2 * 58 + 9, 132, 40, "OBSERVE", "", kr);
  const ks = seg(t, a2, a2 + 0.5);
  sheet(80, 400, 1120, "INCIDENT #3 · ON YOUR PHONE", [
    ["Level observe. Recorded only: nothing will run and nothing will be asked.", C.termInk, seg(t, a2 + 0.2, a2 + 1.6)],
    ["Why: restart_container(media-server): a policy sets it to observe.", C.accent, seg(t, a2 + 1.4, a2 + 3.0)],
  ], ks);
}

function sGo(t, d, cues) {
  K.head("ACTIONS.CONSOLE_APPROVAL", "GO, IN THE CONSOLE", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.5), a1 = at(cues, 1, 5.2);
  browser(80, 170, 700, "plan-console-go", seg(t, a0, a0 + 0.6), { url: "paeon.example.org/ui/incidents/3", crop: [0.161, 0.237, 0.611, 0.443] });
  const kr = seg(t, a1, a1 + 0.5);
  sheet(830, 190, 370, "THE EXECUTOR'S RULES", [
    ["actions:", C.faint], ["  console_approval: true", C.accent, seg(t, a1 + 0.4, a1 + 1.4)],
  ], kr, { stroke: C.accent, titleColor: C.accent });
  K.lock(1176, 220, 18, C.accent, kr);
  K.box(830, 360, 370, 82, "ITS OWN COPY", "the core's copy only shows the buttons", { k: seg(t, a1 + 1.4, a1 + 1.9), bar: C.accent, subColor: C.soft });
  K.box(830, 456, 370, 82, "OFF BY DEFAULT", "Go stays a signed link on your phone", { k: seg(t, a1 + 2.2, a1 + 2.7), bar: C.line, subColor: C.soft });
}

function sSame(t, d, cues) {
  K.head("ONE QUESTION, ONE ANSWER", "THE FIRST ONE COUNTS", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.5), a1 = at(cues, 1, 3.0);
  const f = i => within(cues, 1, i, a1 + i * 4, 0);
  K.log(80, 210, 700, [
    { time: "21:09:12", tag: "code", text: "Waiting for your Go, until 21:39", kind: "approval.requested", a: a0 + 0.2 },
    { time: "21:11:40", tag: "you", text: "marco said Go, in the console", kind: "approval.granted", a: f(0.0) },
    { time: "21:11:41", tag: "sent", text: "Go from the console, told to the phone", kind: "notification.sent", a: f(0.3) },
    { time: "21:11:58", tag: "exec", text: "restart_container(media-server) verified", kind: "action.verified", a: f(0.85) },
  ], t, { lh: 52 });
  K.phone(860, 120, 300, 470, {
    k: seg(t, f(0.3), f(0.3) + 0.6), clock: "21:11",
    title: "Incident #3: Go from the console",
    lines: ["marco answered in the console.", "The buttons of the question", "no longer work."],
  }, seg(t, a0, a0 + 0.5));
}

function sQuiet(t, d, cues) {
  K.head("STILL OPEN?", "QUIET FOR THREE DAYS", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.5), a1 = at(cues, 1, 5.0);
  const h = browser(80, 170, 1120, "explorer-quiet", seg(t, a0, a0 + 0.6), { crop: [0.314, 0.391, 0.669, 0.111] });
  text("The source sends a firing. The recovery never comes.", 80, 210 + h + 30, { size: 18, color: C.soft, alpha: seg(t, a0 + 1.5, a0 + 2.1) });
  // the quiet tag, after the title of the row
  const kq = seg(t, a1 + 0.5, a1 + 1.0);
  if (kq > 0) highlight(80 + 1120 * 0.261, 200 + h * 0.56, 1120 * 0.086, h * 0.16, kq);
  K.lamp(80, 210 + h + 60, 220, 44, "QUIET 3 DAYS", "HOLD", seg(t, a1 + 0.8, a1 + 1.2));
}

function sRecheck(t, d, cues) {
  K.head("RE-CHECK, OR CLOSE", "ASK THE SOURCE AGAIN", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.5), a1 = at(cues, 1, 3.7), a2 = at(cues, 2, 7.3);
  K.board(80, 170, 540, "RE-CHECK · INCIDENT #3", [
    { name: "OPENOBSERVE", note: "last evaluation: normal", state: "GO", label: "RESOLVED", ks: seg(t, a1, a1 + 0.3), kn: seg(t, a0 + 0.6, a0 + 1.1), nx: 190, size: 16, h: 62, lw: 150 },
    { name: "UPTIME-KUMA", note: "cannot be asked", state: "", label: "", ks: 0, kn: seg(t, a0 + 1.2, a0 + 1.7), nx: 190, size: 16, h: 62, lw: 150 },
  ], seg(t, a0, a0 + 0.5));
  text("Resolved through the recorder, the usual way.", 80, 376, { size: 17, color: C.soft, alpha: seg(t, a1 + 0.6, a1 + 1.1) });
  browser(660, 170, 540, "still-open", seg(t, a2, a2 + 0.6), { url: "paeon.example.org/ui/incidents/3", crop: [0.161, 0.437, 0.611, 0.488] });
  K.lamp(80, 400, 240, 46, "CLOSED BY HAND", "HOLD", seg(t, within(cues, 2, 0.4, a2 + 2, 0), within(cues, 2, 0.4, a2 + 2, 0) + 0.4));
  text("who, when and why: in the record", 340, 430, { size: 16, color: C.soft, alpha: seg(t, within(cues, 2, 0.5, a2 + 2.5, 0), within(cues, 2, 0.5, a2 + 2.5, 0) + 0.4) });
  const kf = seg(t, within(cues, 2, 0.8, a2 + 4, 0), within(cues, 2, 0.8, a2 + 4, 0) + 0.4);
  K.lamp(80, 480, 240, 46, "FIRES AGAIN", "NO-GO", kf);
  text("an incident opens again", 340, 510, { size: 16, color: C.soft, alpha: kf });
}

function sReview(t, d, cues) {
  K.head("POSTMORTEMS", "TRIAGE READS THE REVIEWED ONES", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.5), a1 = at(cues, 1, 4.0), a2 = at(cues, 2, 9.5);
  browser(80, 170, 760, "postmortem-review", seg(t, a1, a1 + 0.6), { url: "paeon.example.org/ui/postmortems", crop: [0.161, 0.05, 0.822, 0.52] });
  const kd = seg(t, a0 + 0.3, a0 + 0.8);
  K.lamp(880, 190, 320, 46, "DRAFT", "", kd);
  text("the model's account: triage does not read it", 880, 260, { size: 15, color: C.soft, alpha: kd });
  const kr = seg(t, within(cues, 1, 0.25, a1 + 1, 0), within(cues, 1, 0.25, a1 + 1, 0) + 0.4);
  K.lamp(880, 290, 320, 46, "REVIEWED", "GO", kr);
  text("a person marked it: triage reads it", 880, 360, { size: 15, color: C.soft, alpha: kr });
  ["EDIT THE TEXT", "WRITE THE PROSE AGAIN"].forEach((n, i) => {
    const s = within(cues, 1, 0.55 + i * 0.25, a1 + 2 + i, 0);
    K.box(880, 390 + i * 70, 320, 58, n, null, { k: seg(t, s, s + 0.4), size: 20 });
  });
  text("Every change is in the record, with your name.", 80, 590, { size: 18, color: C.soft, alpha: seg(t, a2, a2 + 0.5) });
}

function sOutro(t, d, cues) {
  const a0 = at(cues, 0, 0.5), a1 = at(cues, 1, 2.4), kb = seg(t, 0.2, 0.8);
  K.lamp(330, 110, 190, 44, "MODELS THINK", "", seg(t, a0, a0 + 0.4));
  K.lamp(545, 110, 190, 44, "YOU DECIDE", "HOLD", seg(t, a1, a1 + 0.4));
  K.lamp(760, 110, 190, 44, "CODE DOES", "GO", seg(t, a0 + 0.8, a0 + 1.2));
  const l1 = seg(t, a0, a0 + 0.6), l2 = seg(t, a1, a1 + 0.6), l3 = seg(t, a1 + 1.4, a1 + 2.0);
  text("MODELS THINK. CODE DOES.", 640, 290 + (1 - outCubic(l1)) * 14, { size: 60, weight: 700, font: COND, align: "center", spacing: "2px", alpha: l1 });
  text("THE CALLS IN BETWEEN ARE YOURS,", 640, 358 + (1 - outCubic(l2)) * 14, { size: 60, weight: 700, font: COND, align: "center", spacing: "2px", alpha: l2 });
  text("FROM THE PHONE OR THE CONSOLE.", 640, 426 + (1 - outCubic(l3)) * 14, { size: 60, weight: 700, font: COND, align: "center", spacing: "2px", color: C.accent, alpha: l3 });
  ctx.save(); ctx.globalAlpha *= kb; ctx.strokeStyle = C.line; ctx.lineWidth = 1.5; ctx.strokeRect(330, 478, 620, 40); ctx.restore();
  text(S.status, 640, 505, { size: 18, weight: 600, font: COND, spacing: "3px", color: C.soft, align: "center", alpha: kb });
  text(S.url, 640, 568, { size: 18, font: MONO, color: C.accent, align: "center", alpha: seg(t, a1 + 1.0, a1 + 1.6) });
}

K.load(["plan-console-go", "explorer-quiet", "still-open", "postmortem-review"]);
VIDEO.create({
  title: { en: "Your hands on it" },
  kicker: { en: "Paeon · 0.5" },
  langs: ["en"],
  theme: K.theme,
  fonts: K.fonts,
  strings: STR,
  background: K.bg,
  poster: "outro",
  scenes: {
    ran: sRan, fate: sFate, go: sGo, same: sSame, quiet: sQuiet,
    recheck: sRecheck, review: sReview, outro: sOutro,
  },
});
K.finish();
