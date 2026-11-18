/* Paeon in two minutes: what it is, and the line between a model that thinks and code
 * that acts. Scenes are pure functions of t, hooked onto the narration's sentences with
 * at(cues, i). The look comes from ../kit.js. Every claim is from the README; every
 * incident, name and time on screen is fictional. */
"use strict";

const STR = {
  en: {
    url: "mmornati.github.io/paeon",
    tagline: "MODELS THINK.  YOU SAY GO.  CODE DOES.",
  },
};

/* A small caption in spaced condensed capitals. */
function cap(s, x, y, k, color = C.faint, o = {}) {
  text(s, x, y, { size: 15, weight: 600, font: COND, color, spacing: "3px", alpha: k, ...o });
}

/* An outlined cell with a centred label (and a padlock when o.lock). */
function cell(x, y, w, h, label, color, k, o = {}) {
  if (k <= 0) return;
  ctx.save(); ctx.globalAlpha *= k; ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.strokeRect(x + 1, y + 1, w - 2, h - 2); ctx.restore();
  if (o.lock) K.lock(x + 22, y + h / 2 + 3, 18, C.accent, k);
  text(label, x + w / 2 + (o.lock ? 12 : 0), y + h / 2 + 7, { size: o.size ?? 20, weight: 600, font: COND, spacing: "2px", align: "center", color: o.ink ?? color, alpha: k });
}

function sHook(t, d, cues) {
  K.head("03:12 · IN THE MORNING", "YOUR PHONE BUZZES", seg(t, 0.2, 0.9));
  const a1 = at(cues, 1, 3.2), a2 = at(cues, 2, 6.5);
  text("03:12", 80, 340, { size: 170, weight: 700, font: COND, color: C.ink, alpha: seg(t, 0.4, 1.2), spacing: "2px" });
  cap("EVERYONE IS ASLEEP. SO ARE YOU.", 84, 384, seg(t, 0.8, 1.6));
  // the two things it could be
  cell(80, 440, 560, 56, "THE NIGHTLY UPDATE, AGAIN?", C.soft, seg(t, a2 + 0.2, a2 + 0.8), { size: 23 });
  cell(80, 510, 560, 56, "OR THE DISK, REALLY FULL THIS TIME?", C.accent, seg(t, a2 + 2.0, a2 + 2.6), { size: 23 });
  // a phone, and one bare alert
  const buzz = clamp(1 - (t - a1) / 0.7), dx = t >= a1 && buzz > 0 ? Math.sin((t - a1) * 70) * 5 * buzz : 0;
  const px = 860 + dx;
  K.phone(px, 150, 300, 440, { clock: "03:12", k: 0 }, seg(t, 0.3, 0.9));
  const kn = seg(t, a1, a1 + 0.5);
  if (kn > 0) {
    const nx = px + 22, ny = 278 + (1 - outCubic(kn)) * 26, nw = 256;
    panel(nx, ny, nw, 74, { r: 14, fill: C.panel2, stroke: C.line, alpha: kn });
    ctx.save(); ctx.globalAlpha *= kn; ctx.fillStyle = C.bad; ctx.fillRect(nx + 14, ny + 15, 10, 10); ctx.restore();
    text("ALERT", nx + 32, ny + 25, { size: 14, weight: 700, font: COND, spacing: "2px", color: C.soft, alpha: kn });
    text("now", nx + nw - 14, ny + 24, { size: 12, color: C.faint, align: "right", alpha: kn });
    text("SLO fast burn", nx + 14, ny + 54, { size: 17, weight: 700, alpha: kn });
  }
  text("That is all it says.", 1010, 420, { size: 15, color: C.faint, align: "center", alpha: seg(t, a1 + 1.6, a1 + 2.2) });
}

function sWhat(t, d, cues) {
  K.head("PAEON · THE HEALER OF THE GODS IN HOMER", "IT SITS NEXT TO YOUR ALERTING", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.4), a1 = at(cues, 1, 2.8), a2 = at(cues, 2, 9.5);
  const src = [
    ["OPENOBSERVE", "polled · Paeon never writes to it", a1 + 0.9],
    ["UPTIME-KUMA", "a webhook notification", a1 + 2.0],
    ["ALERTMANAGER WEBHOOK", "and anything that speaks it", a1 + 3.6],
  ];
  const kp = seg(t, a0 + 0.2, a0 + 0.8);
  K.box(540, 248, 280, 120, "PAEON", "listens · records", { k: kp, color: C.accent, bar: C.accent, size: 34, subColor: C.soft });
  src.forEach(([name, sub, s], i) => {
    const y = 180 + i * 96, k = seg(t, s, s + 0.5), kw = seg(t, s + 0.3, s + 1.0);
    K.box(80, y, 340, 76, name, sub, { k, bar: K.info });
    K.wire([[420, y + 38], [480, y + 38], [480, 278 + i * 30], [540, 278 + i * 30]], kw, C.soft);
    if (kw >= 1) packets(420, y + 38, 480, y + 38, t, { n: 1, period: 1.6, color: K.info, r: 3 });
  });
  // alerts become incidents
  const k2 = seg(t, a2, a2 + 0.6);
  K.wire([[820, 308], [900, 308]], seg(t, a2 + 0.2, a2 + 0.9), C.accent);
  K.box(900, 248, 300, 120, "INCIDENTS", "alerts, grouped", { k: k2, size: 30, subColor: C.soft });
  [0, 1, 2].forEach(i => dot(1130 + i * 20, 286, 5, K.info, seg(t, a2 + 0.6 + i * 0.2, a2 + 0.9 + i * 0.2)));
  // never in the path
  const kl = seg(t, a0 + 0.9, a0 + 1.5);
  line([[80, 478], [1200, 478]], C.line, 1, kl);
  text("NEXT TO YOUR ALERTING, NEVER INSTEAD OF IT.", 80, 530, { size: 38, weight: 700, font: COND, spacing: "1.5px", alpha: kl });
  text("Your existing notifications stay as they are.", 80, 566, { size: 17, color: C.soft, alpha: seg(t, a0 + 1.4, a0 + 2.0) });
}

function sThink(t, d, cues) {
  K.head("TRIAGE · INVESTIGATE", "MODELS THINK", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.4), a1 = at(cues, 1, 5.3), a2 = at(cues, 2, 9.8);
  const done = t > a2 + 0.3;
  K.board(80, 170, 680, "WHAT A MODEL DOES", [
    { name: "TRIAGE", note: "cheap model · one call, no tools", voice: "model", state: "GO", label: "VERDICT", ks: seg(t, a0 + 4.3, a0 + 4.6), kn: seg(t, a0 + 0.2, a0 + 0.8) },
    { name: "INVESTIGATE", note: "strong model · read-only tools", voice: "model", state: done ? "GO" : "RUN", label: done ? "FINDINGS" : "READING", ks: seg(t, a1 + 0.6, a1 + 0.9), kn: seg(t, a1, a1 + 0.6) },
  ], seg(t, 0.3, 0.9));
  ["NOISE", "TRANSIENT", "KNOWN ISSUE", "REAL"].forEach((v, i) =>
    cell(80 + i * 172, 352, 162, 52, v, K.model, seg(t, a0 + [1.4, 2.0, 2.8, 4.0][i], a0 + [1.4, 2.0, 2.8, 4.0][i] + 0.4), { size: 21 }));
  text("Shadow mode: the verdict is recorded, nothing acts on it.", 80, 432, { size: 15, color: C.faint, alpha: seg(t, a0 + 4.4, a0 + 5.0) });
  // tools that can only read
  cap("TOOLS THAT CAN ONLY READ", 80, 478, seg(t, a1 + 1.6, a1 + 2.2), C.accent);
  ["LOGS", "METRICS", "CONTAINERS", "MONITORS"].forEach((v, i) =>
    cell(80 + i * 172, 492, 162, 44, v, C.line, seg(t, a1 + 2.0 + i * 0.3, a1 + 2.4 + i * 0.3), { lock: true, size: 17, ink: C.soft }));
  text("Each behind an allow-list. Results bounded, redacted, recorded.", 80, 566, { size: 15, color: C.faint, alpha: seg(t, a1 + 3.2, a1 + 3.8) });
  // what you get
  const k2 = seg(t, a2, a2 + 0.5);
  panel(800, 170, 400, 410, { r: 4, fill: C.panel, stroke: C.line, alpha: k2 });
  cap("WHAT IT SENDS YOU", 818, 199, k2);
  line([[800, 214], [1200, 214]], C.line, 1, k2);
  [["A ROOT CAUSE", "with a confidence", 0.2], ["THE EVIDENCE", "every claim tied to the tool call behind it", 0.9], ["A PLAN", "actions from a closed catalog", 1.6]].forEach(([l, s, o], i) => {
    const k = seg(t, a2 + o, a2 + o + 0.5), y = 262 + i * 88;
    text(l, 818, y, { size: 30, weight: 700, font: COND, spacing: "2px", color: K.model, alpha: k });
    text(s, 818, y + 28, { size: 15, color: C.soft, alpha: k });
  });
  line([[818, 508], [1182, 508]], C.line, 1, seg(t, a2 + 1.9, a2 + 2.3));
  text("A model can be confidently wrong:", 818, 536, { size: 15, color: C.faint, alpha: seg(t, a2 + 1.9, a2 + 2.3) });
  text("read the evidence, not only the conclusion.", 818, 558, { size: 15, color: C.faint, alpha: seg(t, a2 + 1.9, a2 + 2.3) });
}

function sLine(t, d, cues) {
  K.head("THE SAFETY MODEL", "THE HARD LINE", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.4), a1 = at(cues, 1, 5.0), a2 = at(cues, 2, 10.7), a3 = at(cues, 3, 14.8);
  // the wall
  const kw = seg(t, a0 + 0.3, a0 + 1.6);
  line(partial([[640, 160], [640, 590]], outCubic(kw)), C.accent, 2, 1, [8, 7]);
  cap("WHAT A MODEL MAY THINK", 80, 190, seg(t, a0 + 1.4, a0 + 2.0), K.model);
  cap("WHAT CODE MAY DO", 700, 190, seg(t, a0 + 2.8, a0 + 3.4), C.ink);
  const lit2 = seg(t, a2, a2 + 0.5), lit3 = seg(t, a3, a3 + 0.5);
  K.box(80, 206, 500, 84, "THE CORE", "one process", { k: seg(t, a0 + 1.6, a0 + 2.2), color: lit2 > 0 ? K.model : C.line, bar: K.model });
  K.box(700, 206, 500, 84, "THE EXECUTOR", "a separate process", { k: seg(t, a0 + 3.0, a0 + 3.6), color: lit3 > 0 ? C.good : C.line, bar: C.good });
  // no, no, no
  ["NO SHELL", "NO FREE-FORM COMMAND", "NO WRITE CREDENTIAL"].forEach((s, i) => {
    const o = [0.2, 1.0, 2.4][i], k = seg(t, a1 + o, a1 + o + 0.4), y = 336 + i * 38;
    cross(94, y - 8, 8, C.bad, k);
    text(s, 120, y, { size: 25, weight: 600, font: COND, spacing: "2px", alpha: k });
  });
  text("for a model, in any stage", 412, 412, { size: 15, color: C.bad, alpha: seg(t, a1 + 3.6, a1 + 4.2) });
  // the core
  line([[80, 444], [580, 444]], C.line, 1, lit2);
  [["reads attacker-controlled text", 0.3], ["talks to models", 2.4]].forEach(([s, o], i) => {
    const k = seg(t, a2 + o, a2 + o + 0.5), y = 480 + i * 34;
    dot(92, y - 6, 4, K.model, k);
    text(s, 112, y, { size: 19, color: C.ink, alpha: k });
  });
  text("so it is never trusted with a write credential", 80, 566, { size: 15, color: C.faint, alpha: seg(t, a2 + 3.0, a2 + 3.6) });
  // the executor
  K.lamp(700, 318, 360, 46, "THE ONE WRITE CREDENTIAL", "GO", seg(t, a3 + 1.0, a3 + 1.5));
  cap("BELIEVES ONLY", 700, 412, seg(t, a3 + 3.0, a3 + 3.5));
  line([[700, 424], [1200, 424]], C.line, 1, seg(t, a3 + 3.0, a3 + 3.5));
  const kr = seg(t, a3 + 3.8, a3 + 4.3), kt = seg(t, a3 + 5.6, a3 + 6.1);
  dot(712, 454, 4, C.soft, kr); text("its own copy of the rules", 732, 460, { size: 19, alpha: kr });
  dot(712, 488, 4, C.accent, kt); text("a link you tapped", 732, 494, { size: 19, color: C.accent, alpha: kt });
  text("it trusts nothing the model touched", 700, 566, { size: 15, color: C.faint, alpha: seg(t, a3 + 6.2, a3 + 6.8) });
  // the only thing that crosses
  const kc = seg(t, a3 + 0.2, a3 + 0.9);
  K.wire([[580, 248], [700, 248]], kc, C.soft);
  text("a plan", 608, 236, { size: 13, font: MONO, color: C.soft, align: "center", alpha: kc });
}

function sCatalog(t, d, cues) {
  K.head("ACTING · OFF BY DEFAULT", "THE CATALOG IS CLOSED", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.4), a1 = at(cues, 1, 4.0), e1 = atEnd(cues, 1, 8.7), a2 = at(cues, 2, 9.0);
  const kp = seg(t, 0.3, 0.9), on = seg(t, a1 + 1.4, a1 + 1.9), cfg = seg(t, a2 + 0.2, a2 + 0.7);
  panel(80, 170, 560, 328, { r: 4, fill: C.panel, stroke: C.line, alpha: kp });
  cap("THE CATALOG · SIX ACTIONS EXIST", 98, 199, kp);
  line([[80, 214], [640, 214]], C.line, 1, kp);
  const acts = [["restart_container", "a container"], ["start_container", "a container"], ["unpause_container", "a container"],
                ["reattach_network_alias", "a container"], ["coolify_restart", "a Coolify resource you named"], ["config_change", "a configuration file you named"]];
  acts.forEach(([name, kind], i) => {
    const k = seg(t, a0 + 1.6 + i * 0.22, a0 + 2.0 + i * 0.22), y = 214 + i * 46;
    if (i < 5) line([[80, y + 46], [640, y + 46]], C.line, 1, k * 0.6);
    text(String(i + 1), 100, y + 30, { size: 20, weight: 700, font: COND, color: C.faint, alpha: k });
    const lit = i === 0 ? on : 0;
    text(name, 132, y + 29, { size: 17, font: MONO, color: lit > 0.5 ? C.good : i === 5 && cfg > 0.5 ? C.accent : C.ink, weight: lit > 0.5 ? 600 : 400, alpha: k });
    text(lit > 0.5 ? "on for: worker, media-*" : kind, 622, y + 29, { size: 14, color: lit > 0.5 ? C.good : C.faint, align: "right", alpha: k });
  });
  // the rules file
  const k1 = seg(t, a1, a1 + 0.5);
  panel(700, 170, 500, 148, { r: 4, fill: C.deep, stroke: C.line, alpha: k1 });
  cap("YOUR RULES FILE", 718, 199, k1);
  ["catalog:", "  restart_container:", '    targets: [worker, "media-*"]', "    deny: [media-db]"].forEach((s, i) =>
    text(s, 718, 228 + i * 23, { size: 15, font: MONO, color: C.termInk, alpha: seg(t, a1 + 0.3 + i * 0.2, a1 + 0.7 + i * 0.2) }));
  K.wire([[700, 244], [648, 244]], seg(t, a1 + 1.0, a1 + 1.6), C.good);
  const kx = seg(t, e1 - 1.5, e1 - 1.0);
  K.lamp(700, 334, 300, 44, "IT CANNOT ADD ONE", "NO-GO", kx);
  text("Anything else in a plan stays a sentence for you.", 80, 534, { size: 16, color: C.soft, alpha: kx });
  // configuration is proposed
  K.box(700, 410, 500, 72, "CONFIG_CHANGE", "one value of one YAML file, on a branch", { k: cfg, color: C.accent, bar: C.accent });
  K.lamp(1030, 425, 156, 42, "NEVER APPLIED", "NO-GO", seg(t, a2 + 1.6, a2 + 2.0));
  K.wire([[950, 482], [950, 508]], seg(t, a2 + 2.8, a2 + 3.3), C.soft);
  K.box(700, 510, 500, 72, "A PULL REQUEST", "merging it is the approval", { k: seg(t, a2 + 3.0, a2 + 3.6), color: C.good, bar: C.good });
}

function sGo(t, d, cues) {
  K.head("GO / NO-GO", "ON YOUR PHONE", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.4), a1 = at(cues, 1, 3.2), a2 = at(cues, 2, 8.4);
  K.phone(80, 160, 290, 430, {
    k: seg(t, a0 + 0.6, a0 + 1.2), clock: "03:14", title: "Run this fix?",
    lines: ["worker_stalled", "1. Restart the container", "    (worker)", "Nothing runs without Go."],
    buttons: [{ label: "GO", kind: "go" }, { label: "NO-GO", kind: "no" }],
  }, seg(t, 0.2, 0.8));
  K.guard(420, 250, 0, 0, seg(t, 0.4, 1.0));
  text("the executor's switch", 495, 468, { size: 14, color: C.faint, align: "center", alpha: seg(t, 0.6, 1.2) });
  text("covered until you tap Go", 495, 490, { size: 14, color: C.accent, align: "center", alpha: seg(t, a2 + 0.4, a2 + 1.0) });
  // what the button is
  cap("THE BUTTON", 660, 190, seg(t, a1, a1 + 0.5));
  [["SIGNED", "sent by the executor: the core never sees it", 0.8], ["SINGLE-USE", "it works once", 1.5],
   ["EXPIRING", "after 30 minutes, nothing runs", 2.4], ["EXACT PLAN", "a new plan or a No-go kills it", 3.3]].forEach(([l, s, o], i) => {
    const k = seg(t, a1 + o, a1 + o + 0.4), y = 206 + i * 54;
    K.lamp(660, y, 150, 40, l, "", k);
    text(s, 828, y + 26, { size: 16, color: C.soft, alpha: k });
  });
  // opening is not approving
  const k2 = seg(t, a2, a2 + 0.5);
  line([[660, 440], [1200, 440]], C.line, 1, k2);
  K.lamp(660, 462, 220, 46, "OPEN THE LINK", "", k2);
  K.wire([[880, 485], [936, 485]], seg(t, a2 + 0.6, a2 + 1.2), C.soft);
  K.lamp(940, 462, 260, 46, "NOTHING APPLIED", "HOLD", seg(t, a2 + 1.2, a2 + 1.6));
  text("No answer: nothing happens, and one reminder says so.", 660, 552, { size: 16, color: C.soft, alpha: seg(t, a2 + 1.4, a2 + 1.9) });
}

function sLog(t, d, cues) {
  K.head("THE RECORD", "EVERYTHING IS AN EVENT", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.4), kp = seg(t, 0.3, 0.9);
  panel(80, 162, 1120, 188, { r: 4, fill: C.panel, stroke: C.line, alpha: kp });
  cap("EVENT LOG · APPEND-ONLY · ONE SQLITE FILE", 98, 191, kp);
  text("below: the console, the same log rendered for a person", 1182, 190, { size: 13, font: MONO, color: C.faint, align: "right", alpha: seg(t, a0 + 1.4, a0 + 2.0) });
  line([[80, 206], [1200, 206]], C.line, 1, kp);
  K.log(98, 238, 1084, [
    { time: "03:12:04", tag: "in", text: "SLO fast burn is firing", kind: "an alert", a: a0 + 3.2 },
    { time: "03:12:04", tag: "code", text: "Incident opened", kind: "a decision", a: a0 + 4.0 },
    { time: "03:12:09", tag: "model", text: "Triage says: real", kind: "a model call · cost 0.0019", a: a0 + 4.8 },
    { time: "03:12:31", tag: "tool", text: "Log search returned its rows", kind: "a tool result", a: a0 + 6.6 },
  ], t, { lh: 32, size: 16 });
  K.shot("board", 80, 364, 1120, { k: seg(t, a0 + 1.0, a0 + 1.7), crop: [0.172, 0.17, 0.82, 0.255] });
}

/* A budget meter: a label, a track that fills, one line under it. */
function meter(x, label, sub, frac, color, k) {
  if (k <= 0) return;
  panel(x, 180, 350, 112, { r: 4, fill: C.panel, stroke: C.line, alpha: k });
  text(label, x + 18, 214, { size: 22, weight: 600, font: COND, spacing: "2px", alpha: k });
  ctx.save(); ctx.globalAlpha *= k;
  ctx.fillStyle = C.deep; ctx.fillRect(x + 18, 232, 314, 14);
  ctx.fillStyle = color; ctx.fillRect(x + 18, 232, 314 * clamp(frac), 14);
  ctx.strokeStyle = C.line; ctx.lineWidth = 1; ctx.strokeRect(x + 18.5, 232.5, 313, 13);
  ctx.restore();
  text(sub, x + 18, 274, { size: 14, color: C.faint, alpha: k });
}

function sBounded(t, d, cues) {
  K.head("BOUNDED BY DESIGN", "A SPENT BUDGET NEVER HIDES AN ALERT", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.4), a1 = at(cues, 1, 4.8), a2 = at(cues, 2, 9.0);
  const spent = seg(t, a1, a1 + 0.9);
  meter(80, "BUDGET · PER INCIDENT", "set in the rules file", lerp(0.55 * outCubic(seg(t, a0 + 0.4, a0 + 1.6)), 1, outCubic(spent)), spent >= 1 ? C.accent : C.good, seg(t, a0 + 0.1, a0 + 0.6));
  meter(465, "BUDGET · PER DAY", "set in the rules file", 0.4 * outCubic(seg(t, a0 + 1.6, a0 + 2.8)), C.good, seg(t, a0 + 1.3, a0 + 1.8));
  meter(850, "STORM GUARD · PER HOUR", "plus limits on tool calls and time", 0.25 * outCubic(seg(t, a0 + 2.9, a0 + 4.0)), C.good, seg(t, a0 + 2.6, a0 + 3.1));
  // fails open
  K.lamp(80, 322, 230, 46, "BUDGET SPENT", "HOLD", seg(t, a1 + 0.8, a1 + 1.2));
  K.wire([[310, 345], [366, 345]], seg(t, a1 + 1.4, a1 + 1.9), C.soft);
  K.lamp(370, 322, 270, 46, "TREATED AS REAL", "GO", seg(t, a1 + 1.9, a1 + 2.3));
  text("The alert still reaches you: Paeon fails open.", 664, 353, { size: 19, weight: 600, alpha: seg(t, a1 + 2.4, a1 + 3.0) });
  // what a model may read
  const k2 = seg(t, a2, a2 + 0.5);
  line([[80, 404], [1200, 404]], C.line, 1, k2);
  cap("WHAT A MODEL MAY READ", 80, 436, k2);
  K.box(80, 452, 540, 96, "FIRST · ALLOW-LISTS", "a stream, a metric or a container you did not list cannot be read", { k: seg(t, a2 + 0.1, a2 + 0.6), bar: C.accent, subColor: C.soft });
  K.box(660, 452, 540, 96, "SECOND · REDACTION", "what is read is scrubbed of secret-shaped strings before it leaves", { k: seg(t, a2 + 1.3, a2 + 1.8), bar: C.soft, subColor: C.soft });
}

function sHonest(t, d, cues) {
  K.head("STATUS: ALPHA", "WHAT IT IS NOT, YET", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.4), a1 = at(cues, 1, 7.4), a2 = at(cues, 2, 13.3);
  cap("OUT OF THE BOX", 80, 190, seg(t, a0, a0 + 0.5));
  K.lamp(80, 204, 540, 60, "NO ACTION ON YOUR SERVER", "NO-GO", seg(t, a0 + 1.4, a0 + 1.9));
  para("It records, correlates, triages, investigates and drafts postmortems.", 80, 300, 540, { size: 17, lh: 25, color: C.soft, alpha: seg(t, a0 + 2.0, a0 + 2.6) });
  const kx = seg(t, a0 + 3.6, a0 + 4.2);
  K.box(80, 348, 540, 80, "THE EXECUTOR", "a second process you add on purpose", { k: kx, bar: C.good });
  K.lamp(446, 367, 160, 42, "OFF BY DEFAULT", "", kx);
  // alpha
  cap("WHERE IT STANDS", 700, 190, seg(t, a1, a1 + 0.5));
  K.lamp(700, 204, 500, 60, "ALPHA SOFTWARE", "HOLD", seg(t, a1 + 0.3, a1 + 0.8));
  para("The acting part has not yet run on a real server for long.", 700, 300, 500, { size: 17, lh: 25, color: C.soft, alpha: seg(t, a1 + 1.6, a1 + 2.2) });
  [["APPROVE AND ACT", 2.8], ["EARNED AUTONOMY", 3.6]].forEach(([n, o], i) => {
    const k = seg(t, a1 + o, a1 + o + 0.5), y = 348 + i * 40;
    line([[700, y + 40], [1200, y + 40]], C.line, 1, k);
    text(n, 700, y + 27, { size: 20, weight: 600, font: COND, spacing: "2px", alpha: k });
    text("BUILT, NOT YET PROVEN IN USE", 1200, y + 26, { size: 15, weight: 600, font: COND, spacing: "1.5px", color: C.accent, align: "right", alpha: k });
  });
  // the advice
  const k2 = seg(t, a2, a2 + 0.6);
  line([[80, 462], [1200, 462]], C.line, 1, k2);
  text("RUN IT NEXT TO YOUR ALERTING, NEVER INSTEAD OF IT.", 640, 524 + (1 - outCubic(k2)) * 12, { size: 44, weight: 700, font: COND, align: "center", spacing: "1.5px", alpha: k2 });
  text("Your existing notifications stay as they are.", 640, 564, { size: 17, color: C.soft, align: "center", alpha: seg(t, a2 + 1.4, a2 + 2.0) });
}

function sOutro(t, d, cues) {
  const a0 = at(cues, 0, 0.5), a1 = at(cues, 1, 5.0), kb = seg(t, 0.2, 0.8);
  const rows = [
    ["ALERTS", "recorded as they come, grouped into incidents", "code", 0.3],
    ["MODELS", "triage and investigate, with tools that can only read", "model", 0.6],
    ["YOU", "Go / No-go on your phone", "you", 0.9],
    ["CODE", "a closed catalog, checked by a separate executor", "code", 1.2],
    ["RECORD", "one append-only log of every step", "code", 1.5],
    ["FOOTPRINT", "one container · one SQLite file · one YAML file", "code", a0 + 0.9],
  ];
  K.board(80, 76, 1120, "PAEON · ALL STATIONS", rows.map(([name, note, voice, s]) => ({
    name, note, voice, state: "GO", ks: seg(t, s + 0.3, s + 0.6), kn: seg(t, s, s + 0.5), nx: 230, size: 19,
  })), kb);
  const k1 = seg(t, a1, a1 + 0.7);
  text(S.tagline, 640, 530 + (1 - outCubic(k1)) * 14, { size: 56, weight: 700, font: COND, align: "center", spacing: "2px", alpha: k1 });
  text(S.url, 640, 578, { size: 18, font: MONO, color: C.accent, align: "center", alpha: seg(t, a1 + 1.6, a1 + 2.2) });
}

K.load(["board"]);
VIDEO.create({
  title: { en: "Paeon in two minutes" },
  kicker: { en: "Paeon · explained" },
  langs: ["en"],
  theme: K.theme,
  fonts: K.fonts,
  strings: STR,
  background: K.bg,
  poster: "outro",
  scenes: {
    hook: sHook, what: sWhat, think: sThink, line: sLine, catalog: sCatalog,
    go: sGo, log: sLog, bounded: sBounded, honest: sHonest, outro: sOutro,
  },
});
K.finish();
