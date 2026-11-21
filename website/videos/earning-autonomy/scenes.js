/* Earning autonomy: how one action goes from asking every time to running alone, and what
 * takes that back. Scenes are pure functions of t, hooked onto the narration's sentences with
 * at(cues, i). The look comes from ../kit.js. Every name and count here is fictional; every
 * rule comes from docs/acting.md. */
"use strict";

const STR = {
  en: {
    url: "mmornati.github.io/paeon",
    status: "BUILT · NOT YET PROVEN IN USE",
  },
};

/* A moment inside sentence i: f = 0 is its first word, 1 its last. */
const within = (cues, i, f, s, e) => lerp(at(cues, i, s), atEnd(cues, i, e), f);

const label = (s, x, y, k, color = C.faint, o = {}) =>
  text(s, x, y, { size: 15, weight: 600, font: COND, color, spacing: "3px", alpha: k, ...o });

/* A level's lamp. Observe is chalk (code records), suggest an outline, approve amber, auto green. */
function level(x, y, w, h, name, k = 1, lit = 1) {
  if (k <= 0) return;
  if (lit <= 0) return K.lamp(x, y, w, h, name, "", k * 0.8);
  if (name === "APPROVE") return K.lamp(x, y, w, h, name, "HOLD", k);
  if (name === "AUTO") return K.lamp(x, y, w, h, name, "GO", k);
  ctx.save(); ctx.globalAlpha *= clamp(k);
  if (name === "OBSERVE") { ctx.fillStyle = C.ink; ctx.fillRect(x, y, w, h); }
  else { ctx.strokeStyle = C.ink; ctx.lineWidth = 2; ctx.strokeRect(x + 1, y + 1, w - 2, h - 2); }
  text(name, x + w / 2, y + h / 2 + 8, { size: 22, weight: 700, font: COND, color: name === "OBSERVE" ? C.deep : C.ink, align: "center", spacing: "2px" });
  ctx.restore();
}

/* The policy line of this story, in monospace. Returns where its four fields sit: [[x, w], …]. */
function policy(x, y, lvl, k, o = {}) {
  const size = o.size ?? 16;
  const parts = ["- { ", 'alert: "slo_traefik_5xx"', ", ", "action: restart_container", ", ", 'target: "media-server"', ", ", "level: " + lvl, " }"];
  const at4 = [];
  let cx = x;
  parts.forEach((p, i) => {
    const w = measure(p, size, 400, MONO), field = i % 2 === 1;
    const color = field && i === 7 && o.levelColor ? o.levelColor : field ? C.ink : C.faint;
    text(p, cx, y, { size, font: MONO, color, alpha: k, weight: field && i === 7 && o.levelColor ? 700 : 400 });
    if (field) at4.push([cx, w]);
    cx += w;
  });
  return at4;
}

function sQuestion(t, d, cues) {
  K.head("AGAIN", "THE SAME QUESTION, THE SAME GO", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.8), a1 = at(cues, 1, 6.9), a2 = at(cues, 2, 8.7);
  const row = (name, note, s, g) => ({ name, note, voice: "you", state: "GO", label: "GO", ks: seg(t, g, g + 0.3), kn: seg(t, s, s + 0.5), nx: 200, size: 17, h: 64 });
  K.board(80, 180, 700, "SLO_TRAEFIK_5XX · RESTART_CONTAINER · MEDIA-SERVER", [
    row("TUESDAY", "asked: restart media-server?", a0 + 0.6, a0 + 1.4),
    row("SATURDAY", "asked: restart media-server?", a0 + 2.0, a0 + 2.8),
    row("TONIGHT", "asked, again", a0 + 3.6, a1 + 0.7),
  ], seg(t, 0.4, 1.0));
  K.phone(860, 150, 300, 440, {
    k: seg(t, a0 + 0.3, a0 + 0.9), clock: "21:08",
    title: "Incident #7: run this fix?",
    lines: ["slo_traefik_5xx", "1. Restart the container", "    (media-server)", "Nothing runs without Go."],
    buttons: [{ label: "GO", kind: "go", pressed: seg(t, a1 + 0.1, a1 + 0.8) }, { label: "NO-GO", kind: "no" }],
  }, seg(t, 0.3, 0.9));
  const kq = seg(t, a2, a2 + 0.6);
  text("COULD IT STOP ASKING?", 80, 530 + (1 - outCubic(kq)) * 14, { size: 68, weight: 700, font: COND, spacing: "1px", alpha: kq });
}

function sLevels(t, d, cues) {
  K.head("FOUR LEVELS", "FROM RECORDING TO RUNNING ALONE", seg(t, 0.2, 0.9));
  const a = [0, 1, 2, 3, 4].map(i => at(cues, i, [0.5, 3.1, 5.4, 7.6, 10.5][i]));
  const cols = [
    ["OBSERVE", "Records what would have been done.", "Nothing is sent, nothing runs."],
    ["SUGGEST", "Tells you, in the analysis you receive.", "No button."],
    ["APPROVE", "Waits for your Go.", "Go and No-go. Nothing runs without Go."],
    ["AUTO", "Runs, verifies, then tells you.", "With Undo where there is an inverse, and \"This was wrong\"."],
  ];
  cols.forEach(([name, what, more], i) => {
    const x = 80 + i * 285, kb = seg(t, 0.4 + i * 0.12, 0.9 + i * 0.12), k = seg(t, a[i], a[i] + 0.5);
    level(x, 190, 265, 60, name, kb, k);
    panel(x, 262, 265, 150, { r: 4, fill: C.panel, stroke: C.line, alpha: kb });
    const hh = para(what, x + 18, 298, 229, { size: 19, lh: 26, weight: 600, alpha: k });
    para(more, x + 18, 304 + hh, 229, { size: 15, lh: 21, color: name === "APPROVE" ? C.accent : C.soft, alpha: seg(t, a[i] + 0.5, a[i] + 1.0) });
  });
  // the default
  const kd = seg(t, a[4], a[4] + 0.5);
  if (kd > 0) {
    ctx.save(); ctx.globalAlpha *= kd; ctx.strokeStyle = C.accent; ctx.lineWidth = 2; ctx.strokeRect(365 - 9, 181, 283, 240); ctx.restore();
    panel(365 + 62, 410, 141, 24, { r: 0, fill: C.bg, stroke: false, alpha: kd });
    text("THE DEFAULT", 365 + 132, 428, { size: 17, weight: 700, font: COND, color: C.accent, spacing: "3px", align: "center", alpha: kd });
  }
  // the scale
  const ks = seg(t, 1.0, 2.2);
  K.wire([[80, 484], [1200, 484]], ks, C.line);
  label("MOST CAREFUL", 80, 514, seg(t, 1.0, 1.6));
  label("RUNS ALONE", 1200, 514, seg(t, 1.8, 2.4), C.faint, { align: "right" });
  text("A plan with several steps runs at the most careful level of its steps.", 640, 562, { size: 17, color: C.soft, align: "center", alpha: seg(t, a[3] + 1.2, a[3] + 1.8) });
}

function sTuple(t, d, cues) {
  K.head("PER ALERT · PER ACTION · PER CONTAINER", "ONE POLICY LINE", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.5), e0 = atEnd(cues, 0, 4.1), a1 = at(cues, 1, 4.4);
  const kp = seg(t, 0.4, 1.0);
  panel(80, 180, 1120, 150, { r: 4, fill: C.deep, stroke: C.line, alpha: kp });
  label("THE RULES FILE", 100, 209, kp);
  text("actions:", 104, 244, { size: 16, font: MONO, color: C.faint, alpha: kp });
  text("  policies:", 104, 272, { size: 16, font: MONO, color: C.faint, alpha: kp });
  const f = policy(104 + measure("    ", 16, 400, MONO), 300, "approve", kp, { levelColor: C.accent });
  // the three fields the level hangs on, then the level
  const names = [
    ["ONE ALERT", "its title, as a regular expression", C.ink, lerp(a0, e0, 0.38)],
    ["ONE ACTION", "one of the six in the catalog", C.ink, lerp(a0, e0, 0.62)],
    ["ONE CONTAINER", "the target, by name", C.ink, lerp(a0, e0, 0.82)],
    ["ITS LEVEL", "here: wait for your Go", C.accent, a1 + 0.3],
  ];
  names.forEach(([n, sub, color, s], i) => {
    const k = seg(t, s, s + 0.45), [fx, fw] = f[i], bx = 80 + i * 285;
    if (k <= 0) return;
    line([[fx, 308], [fx + fw, 308]], color, 2, k);
    line(partial([[fx + fw / 2, 308], [fx + fw / 2, 372], [bx + 132, 372], [bx + 132, 400]], k), color, 1.5, 0.8);
    K.box(bx, 400, 265, 92, n, sub, { k, color: i === 3 ? C.accent : C.line, bar: color, subColor: C.soft });
  });
  text("First match wins. Where no line matches, the level is suggest.", 80, 548, { size: 17, color: C.soft, alpha: seg(t, a1 + 1.6, a1 + 2.2) });
}

/* Five runs of one action on one container for one alert. n of them are lit (fractional). */
function runs(t, starts, y) {
  starts.forEach((s, i) => {
    const x = 80 + i * 228, kb = seg(t, 0.4 + i * 0.1, 0.9 + i * 0.1), k = seg(t, s, s + 0.35);
    panel(x, y, 208, 128, { r: 4, fill: C.panel, stroke: k > 0 ? C.good : C.line, lw: k > 0 ? 2 : 1, alpha: kb });
    text("RUN " + (i + 1), x + 16, y + 30, { size: 20, weight: 600, font: COND, spacing: "2px", color: C.soft, alpha: kb });
    text("you said Go", x + 192, y + 29, { size: 14, color: C.accent, align: "right", alpha: kb });
    line([[x, y + 44], [x + 208, y + 44]], C.line, 1, kb);
    if (k > 0) K.lamp(x + 14, y + 62, 180, 48, "VERIFIED", "GO", k);
    else K.lamp(x + 14, y + 62, 180, 48, "STANDBY", "", kb * 0.7);
  });
}

function sRecord(t, d, cues) {
  K.head("THE TRACK RECORD", "EVERY APPROVED RUN IS RECORDED", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.5), e0 = atEnd(cues, 0, 6.2);
  const starts = [0, 1, 2, 3, 4].map(i => a0 + 0.3 + i * 0.42);
  runs(t, starts, 180);
  const n = starts.filter(s => t >= s + 0.2).length;
  label("IN A ROW", 1200, 344, seg(t, a0, a0 + 0.5), C.faint, { align: "right" });
  text(String(n), 1104, 350, { size: 40, weight: 700, font: COND, color: n ? C.good : C.faint, align: "right", alpha: seg(t, a0, a0 + 0.5) });
  label("restart_container(media-server) · slo_traefik_5xx", 80, 344, seg(t, 0.5, 1.1), C.faint, { font: MONO, size: 13, spacing: "0px", weight: 400 });
  // what a run can turn out to be
  const kinds = [
    ["VERIFIED", "GO", "running and healthy after the step", 0.44],
    ["FAILED", "NO-GO", "the verification did not pass", 0.58],
    ["UNDONE", "HOLD", "you tapped Undo", 0.70],
    ["THIS WAS WRONG", "HOLD", "it ran, and should not have", 0.83],
  ];
  kinds.forEach(([name, state, sub, fr], i) => {
    const s = lerp(a0, e0, fr), k = seg(t, s, s + 0.4), x = 80 + i * 285;
    K.lamp(x, 392, 265, 50, name, state, k);
    text(sub, x + 132, 470, { size: 15, color: C.soft, align: "center", alpha: k });
  });
  line([[80, 508], [1200, 508]], C.line, 1, seg(t, 0.6, 1.2));
  text("Paeon never changes a policy. It keeps the record.", 80, 552, { size: 19, weight: 600, alpha: seg(t, a0 + 2.6, a0 + 3.2) });
}

const EARNED = { x: 80, y: 168, w: 1120, crop: [0.161, 0.174, 0.822, 0.215] };

function sEarned(t, d, cues) {
  K.head("EARNED", "FIVE IN A ROW: A PROPOSAL", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.5), e0 = atEnd(cues, 0, 12), a1 = at(cues, 1, 12.3);
  // the five runs, small, in the header
  [0, 1, 2, 3, 4].forEach(i => {
    const s = a0 + 0.5 + i * 0.5;
    K.lamp(896 + i * 62, 92, 54, 36, String(i + 1), seg(t, s, s + 0.01) > 0 ? "GO" : "", seg(t, 0.4, 0.9));
  });
  K.shot("autonomy", EARNED.x, EARNED.y, EARNED.w, { k: seg(t, a0 + 3.2, a0 + 3.9), crop: EARNED.crop, shadow: false });
  // the line it shows
  const s1 = lerp(a0, e0, 0.72), kl = seg(t, s1, s1 + 0.5);
  panel(80, 356, 1120, 56, { r: 4, fill: C.deep, stroke: kl > 0 ? C.good : C.line, lw: 1.5, alpha: kl });
  policy(104, 389, "auto", kl, { levelColor: C.good, size: 15 });
  label("THE LINE TO ADD", 1182, 390, kl, C.good, { align: "right" });
  // who does what
  const kp = seg(t, a1, a1 + 0.5), ky = seg(t, a1 + 1.0, a1 + 1.5);
  K.box(80, 434, 540, 92, "PAEON PROPOSES", "it keeps the record, and never changes a policy", { k: kp, subColor: C.soft });
  K.box(660, 434, 540, 92, "YOU MAKE THE CHANGE", "above the line it overrides: commit, then deploy to the executor", { k: ky, color: C.accent, bar: C.accent, subColor: C.soft });
  text("The console's Autonomy page · five in a row, within ninety days", 80, 568, { size: 13, font: MONO, color: C.faint, alpha: seg(t, a0 + 3.6, a0 + 4.2) });
}

function sLimits(t, d, cues) {
  K.head("LIMITS", "WHERE AUTO NEVER APPLIES", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.5), e0 = atEnd(cues, 0, 8.9);
  const row = (name, note, fr) => {
    const s = lerp(a0, e0, fr);
    return { name, note, state: "HOLD", label: "APPROVE", ks: seg(t, s + 0.7, s + 1.0), kn: seg(t, s, s + 0.5), k: seg(t, s - 0.2, s + 0.3), nx: 420, size: 18, h: 84, lw: 150 };
  };
  K.board(80, 180, 1120, "THE POLICY SAYS AUTO · THE PLAN IS HELD AT APPROVE WHEN", [
    row("SEVERAL ALERT CLASSES", "the incident has more than one", 0.12),
    row("LOW CONFIDENCE", "the investigation is under the threshold you set", 0.52),
    row("ALREADY RAN ONCE", "a plan already ran for that incident", 0.76),
  ], seg(t, 0.3, 0.9));
  text("Whatever the policies say. The reason is recorded with the plan, and shown on the incident's page.", 80, 538, { size: 17, color: C.soft, alpha: seg(t, e0 - 1.6, e0 - 1.0) });
}

function sDemoted(t, d, cues) {
  K.head("TAKEN BACK", "ONE OF THESE IS ENOUGH", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.5), e0 = atEnd(cues, 0, 5.5), a1 = at(cues, 1, 5.9);
  const kb = seg(t, 0.4, 1.0), drop = lerp(a0, e0, 0.86);
  // the three ways to lose it
  [["FAILED VERIFICATION", "NO-GO", 0.04], ["UNDO", "HOLD", 0.42], ["THIS WAS WRONG", "HOLD", 0.62]].forEach(([name, state, fr], i) => {
    const y = 196 + i * 78, s = lerp(a0, e0, fr), k = seg(t, s, s + 0.35);
    if (k > 0) K.lamp(80, y, 330, 58, name, state, k); else K.lamp(80, y, 330, 58, name, "", kb * 0.8);
    K.wire([[410, y + 29], [480, y + 29], [480, 303], [546, 303]], seg(t, s + 0.2, s + 0.8), state === "NO-GO" ? C.bad : C.accent, 1.5);
  });
  // the level, falling one slot
  K.lamp(560, 196, 260, 76, "AUTO", "", kb * 0.6);
  K.lamp(560, 334, 260, 76, "APPROVE", "", kb * 0.6);
  const kd = inOut(seg(t, drop, drop + 0.9));
  K.lamp(560, lerp(196, 334, kd), 260, 76, kd < 0.5 ? "AUTO" : "APPROVE", kd < 0.5 ? "GO" : "HOLD", kb);
  // file against what is in force
  const kf = seg(t, drop + 0.9, drop + 1.4);
  label("IN THE RULES FILE", 880, 222, kb);
  text("level: auto", 880, 254, { size: 19, font: MONO, color: C.good, alpha: kb });
  label("IN FORCE", 880, 360, kb);
  text(kd < 0.5 ? "auto" : "approve", 880, 392, { size: 19, font: MONO, color: kd < 0.5 ? C.good : C.accent, alpha: kb });
  if (kf > 0) pill("demoted", 990, 386, { fill: C.deep, stroke: C.bad, color: C.bad, size: 13, alpha: kf });
  text("whatever the file says", 880, 286, { size: 15, color: C.soft, alpha: kf });
  // the way back
  const kr = seg(t, a1 + 2.4, a1 + 2.9);
  label("UNTIL YOU LIFT IT · NEXT TO THE EXECUTOR", 80, 470, kr, C.accent);
  panel(80, 484, 1120, 54, { r: 4, fill: C.deep, stroke: C.line, alpha: kr });
  text("$ ", 100, 517, { size: 15, font: MONO, color: C.faint, alpha: kr });
  text(typed("docker compose exec paeon-executor paeon reinstate slo_traefik_5xx restart_container media-server", seg(t, a1 + 2.7, a1 + 4.4)), 118, 517, { size: 15, font: MONO, color: C.termInk, alpha: kr });
  text("A restart cannot be undone, which is why \"This was wrong\" exists.", 80, 574, { size: 15, color: C.faint, alpha: seg(t, a1 + 0.4, a1 + 1.0) });
}

function sStop(t, d, cues) {
  K.head("THE KILL SWITCH", "EVERYTHING DROPS TO OBSERVE", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.5), e0 = atEnd(cues, 0, 8.8), a1 = at(cues, 1, 9.1), a2 = at(cues, 2, 11.4);
  const way = (name, note, fr) => {
    const s = lerp(a0, e0, fr);
    return { name, note, state: "NO-GO", label: "STOP", ks: seg(t, s + 0.3, s + 0.6), kn: seg(t, s, s + 0.4), nx: 196, size: 16, h: 60, lw: 96 };
  };
  K.board(80, 180, 640, "FOUR WAYS TO ENGAGE IT", [
    way("NOTIFICATION", "Stop automation, on every result", 0.22),
    way("CONSOLE", "the button on the Actions page", 0.52),
    way("COMMAND", "paeon stop", 0.70),
    way("ENVIRONMENT", "PAEON_KILL_SWITCH=1", 0.82),
  ], seg(t, 0.3, 0.9));
  // the switch goes back to hold, and its cover closes
  const off = seg(t, a1 + 0.1, a1 + 0.8);
  K.guard(770, 196, 1 - seg(t, a1 + 0.7, a1 + 1.4), 1 - off, seg(t, 0.4, 1.0));
  text("the executor's switch", 845, 412, { size: 14, color: C.faint, align: "center", alpha: seg(t, 0.6, 1.2) });
  // every level falls to observe
  ["AUTO", "APPROVE", "SUGGEST", "OBSERVE"].forEach((name, i) => {
    const y = 190 + i * 58, kb = seg(t, 0.5 + i * 0.1, 1.0 + i * 0.1);
    if (name === "OBSERVE") level(980, y, 220, 46, name, kb, off > 0.6 ? 1 : 0);
    else level(980, y, 220, 46, name, kb * (1 - off * 0.45), off > 0.6 ? 0 : 1);
  });
  text("only recorded, until you resume", 1090, 446, { size: 14, color: C.soft, align: "center", alpha: seg(t, a1 + 0.8, a1 + 1.3) });
  text("Plans that wait are cancelled. A Go already given is not run.", 80, 508, { size: 17, color: C.soft, alpha: seg(t, a1 + 0.9, a1 + 1.5) });
  // the way back
  const kr = seg(t, a2, a2 + 0.5);
  panel(80, 526, 640, 52, { r: 4, fill: C.deep, stroke: C.line, alpha: kr });
  text("$ ", 100, 558, { size: 15, font: MONO, color: C.faint, alpha: kr });
  text(typed("docker compose exec paeon-executor paeon resume", seg(t, a2 + 0.3, a2 + 1.5)), 118, 558, { size: 15, font: MONO, color: C.termInk, alpha: kr });
  label("ONLY YOU CAN RELEASE IT", 770, 548, kr, C.accent, { size: 19 });
  text("Neither the console nor the core can.", 770, 574, { size: 15, color: C.soft, alpha: seg(t, a2 + 0.8, a2 + 1.3) });
}

function sOutro(t, d, cues) {
  const a0 = at(cues, 0, 0.5), a1 = at(cues, 1, 4.2), kb = seg(t, 0.2, 0.8);
  // the whole path on one line: five verified runs, your commit, auto; and the way down
  [0, 1, 2, 3, 4].forEach(i => K.lamp(80 + i * 106, 96, 96, 44, "VERIFIED", "GO", seg(t, 0.3 + i * 0.15, 0.5 + i * 0.15)));
  K.wire([[612, 118], [652, 118]], seg(t, 1.0, 1.4), C.soft);
  K.lamp(660, 96, 180, 44, "YOUR COMMIT", "HOLD", seg(t, 1.3, 1.6));
  K.wire([[848, 118], [888, 118]], seg(t, 1.6, 2.0), C.soft);
  K.lamp(896, 96, 150, 44, "AUTO", "GO", seg(t, 1.9, 2.2));
  const kt = seg(t, a1, a1 + 0.5);
  K.wire([[971, 146], [971, 184]], seg(t, a1 + 0.1, a1 + 0.6), C.bad, 2, [6, 5]);
  K.lamp(896, 190, 150, 44, "APPROVE", "HOLD", seg(t, a1 + 0.5, a1 + 0.8));
  text("one failure, one Undo, or one \"This was wrong\"", 878, 218, { size: 16, color: C.soft, align: "right", alpha: kt });
  const line1 = seg(t, a0, a0 + 0.6), line2 = seg(t, a0 + 1.3, a0 + 1.9), line3 = seg(t, a1, a1 + 0.6);
  text("AUTONOMY IS EARNED,", 640, 320 + (1 - outCubic(line1)) * 14, { size: 58, weight: 700, font: COND, align: "center", spacing: "2px", alpha: line1 });
  text("PER ACTION AND PER CONTAINER.", 640, 384 + (1 - outCubic(line2)) * 14, { size: 58, weight: 700, font: COND, align: "center", spacing: "2px", alpha: line2 });
  text("AND IT IS YOURS TO TAKE BACK.", 640, 448 + (1 - outCubic(line3)) * 14, { size: 58, weight: 700, font: COND, align: "center", spacing: "2px", color: C.accent, alpha: line3 });
  // honest status, from the README's roadmap
  ctx.save(); ctx.globalAlpha *= kb; ctx.strokeStyle = C.line; ctx.lineWidth = 1.5; ctx.strokeRect(440, 492, 400, 40); ctx.restore();
  text(S.status, 640, 519, { size: 19, weight: 600, font: COND, spacing: "3px", color: C.soft, align: "center", alpha: kb });
  text(S.url, 640, 578, { size: 18, font: MONO, color: C.accent, align: "center", alpha: seg(t, a1 + 0.8, a1 + 1.4) });
}

K.load(["autonomy"]);
VIDEO.create({
  title: { en: "Earning autonomy" },
  kicker: { en: "Paeon · acting" },
  langs: ["en"],
  theme: K.theme,
  fonts: K.fonts,
  strings: STR,
  background: K.bg,
  poster: "outro",
  scenes: {
    question: sQuestion, levels: sLevels, tuple: sTuple, record: sRecord, earned: sEarned,
    limits: sLimits, demoted: sDemoted, stop: sStop, outro: sOutro,
  },
});
K.finish();
