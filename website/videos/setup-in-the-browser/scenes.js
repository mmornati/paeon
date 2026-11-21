/* Set up in the browser: from three variables to a configured Paeon, in the console. Scenes are
 * pure functions of t, hooked onto the narration's sentences with at(cues, i). The look comes
 * from ../kit.js. Every address, token and name here is fictional; every rule comes from
 * docs/console.md (0.4.0). */
"use strict";

const STR = {
  en: {
    url: "mmornati.github.io/paeon",
    status: "PAEON 0.4.0 · OFF UNTIL YOU TURN IT ON",
  },
};

/* A moment inside sentence i: f = 0 is its first word, 1 its last. */
const within = (cues, i, f, s, e) => lerp(at(cues, i, s), atEnd(cues, i, e), f);

const label = (s, x, y, k, color = C.faint, o = {}) =>
  text(s, x, y, { size: 15, weight: 600, font: COND, color, spacing: "3px", alpha: k, ...o });

/* A browser window around a screenshot of the console. Returns the height drawn. */
function browser(x, y, w, name, k, o = {}) {
  if (k <= 0) return 0;
  const bar = 30;
  panel(x, y, w, bar + 8, { r: 6, fill: C.panel2, stroke: C.line, alpha: k });
  [C.bad, C.warn, C.good].forEach((c, i) => dot(x + 16 + i * 14, y + 15, 4.5, c, k * 0.8));
  text(o.url ?? "paeon.example.org/ui", x + w / 2, y + 20, { size: 12, font: MONO, color: C.faint, align: "center", alpha: k });
  return bar + K.shot(name, x, y + bar, w, { k, crop: o.crop, shadow: o.shadow });
}

/* A form field drawn in the board's world: a label, then a typed value. */
function field(x, y, w, name, value, k, kv, o = {}) {
  if (k <= 0) return;
  label(name, x, y, k, C.faint);
  panel(x, y + 10, w, 40, { r: 4, fill: C.deep, stroke: o.color ?? C.line, lw: 1.5, alpha: k });
  const v = o.secret ? "•".repeat(Math.round(value.length * clamp(kv))) : typed(value, kv);
  text(v, x + 14, y + 37, { size: 16, font: MONO, color: o.ink ?? C.termInk, alpha: k });
  if (o.lock) K.lock(x + w - 22, y + 32, 18, o.lockColor ?? C.accent, k);
}

function sTwo(t, d, cues) {
  K.head("CONFIGURE", "ONE FILE, OR THE BROWSER", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.8), a1 = at(cues, 1, 5.8), kf = seg(t, a0, a0 + 0.6);
  // the rules file, in git
  panel(80, 190, 500, 330, { r: 4, fill: C.deep, stroke: C.line, alpha: kf });
  label("PAEON.YAML · IN GIT", 100, 222, kf);
  line([[80, 236], [580, 236]], C.line, 1, kf);
  const yaml = [
    ["sources:", C.faint], ["  prom:", C.ink], ["    type: prometheus", C.soft], ["    url: http://prometheus:9090", C.soft],
    ["notifiers:", C.faint], ["  phone:", C.ink], ["    type: ntfy", C.soft], ["    topic: paeon", C.soft],
  ];
  yaml.forEach(([s, c], i) => text(typed(s, seg(t, a0 + 0.4 + i * 0.25, a0 + 0.8 + i * 0.25)), 104, 270 + i * 30, { size: 16, font: MONO, color: c, alpha: kf }));
  // the browser, new in 0.4
  const kb = seg(t, a1, a1 + 0.6);
  browser(640, 190 + (1 - outCubic(kb)) * 16, 560, "sources", kb, { crop: [0.155, 0, 0.835, 0.64], shadow: false });
  K.lamp(1040, 140, 160, 40, "NEW IN 0.4", "HOLD", seg(t, a1 + 0.6, a1 + 1.0));
  text("Both, side by side. The file is never written.", 80, 572, { size: 18, color: C.soft, alpha: seg(t, a1 + 1.6, a1 + 2.2) });
}

function sStart(t, d, cues) {
  K.head("A NEW SERVER", "THREE VARIABLES", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.5), a1 = at(cues, 1, 3.5), a2 = at(cues, 2, 11);
  const f = i => within(cues, 1, i, a1, a1 + 7);
  terminal(80, 180, 720, 300, t, [
    { p: "", s: "PAEON_INGEST_TOKEN=3f9c…a71e", a: f(0.0), b: f(0.25), c: C.termInk },
    { p: "", s: "PAEON_SECRET_KEY=b62d…09f4", a: f(0.32), b: f(0.62), c: C.termInk },
    { p: "", s: "PAEON_CONSOLE=local", a: f(0.72), b: f(0.95), c: C.accent, w: 700 },
    { p: "$ ", s: "paeon serve", a: a2 - 0.2, b: a2 + 0.4 },
  ], { title: ".env", alpha: seg(t, a0, a0 + 0.5), size: 18, lh: 40 });
  const notes = [
    ["THE INGEST TOKEN", "what your alert senders present", f(0.05)],
    ["THE SECRET KEY", "seals what the console stores", f(0.4)],
    ["CONSOLE = LOCAL", "on, with its own password", f(0.78)],
  ];
  notes.forEach(([n, sub, s], i) => K.box(840, 190 + i * 96, 360, 82, n, sub, { k: seg(t, s, s + 0.4), bar: i === 2 ? C.accent : C.line, color: i === 2 ? C.accent : C.line, subColor: C.soft }));
  // no rules file
  const kn = seg(t, a2, a2 + 0.5);
  if (kn > 0) {
    K.box(80, 512, 300, 64, "PAEON.YAML", null, { k: kn * 0.6, ink: C.faint });
    line([[92, 548], [368, 540]], C.bad, 3, kn);
    K.lamp(400, 522, 200, 44, "NOT NEEDED", "GO", seg(t, a2 + 0.4, a2 + 0.8));
  }
}

function sCode(t, d, cues) {
  K.head("THE FIRST VISIT", "A CODE FROM THE LOG", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.5), a1 = at(cues, 1, 4.4), a2 = at(cues, 2, 10.9);
  terminal(80, 180, 560, 230, t, [
    { p: "", s: "Uvicorn running on 0.0.0.0:8080", a: 0.3, c: C.faint },
    { p: "", s: "The console has no admin", a: a1, b: a1 + 0.6, c: C.accent },
    { p: "", s: "password yet. Open /ui/setup", a: a1 + 0.5, b: a1 + 1.1, c: C.accent },
    { p: "", s: "and enter this code:", a: a1 + 1.0, b: a1 + 1.5, c: C.accent },
    { p: "", s: "q7Rm2xKc9TbE", a: a1 + 1.5, b: a1 + 2.0, c: C.ink, w: 700 },
  ], { title: "docker logs paeon", alpha: seg(t, 0.3, 0.8), size: 16, lh: 30 });
  text("Whoever reads this log owns the server.", 80, 452, { size: 18, color: C.soft, alpha: seg(t, within(cues, 1, 0.6, a1 + 3, 0), within(cues, 1, 0.75, a1 + 4, 0)) });
  const kb = seg(t, a0 + 0.2, a0 + 0.8);
  browser(700, 160, 500, "setup", kb, { crop: [0.355, 0.1, 0.29, 0.343], url: "paeon.example.org/ui/setup" });
  K.wire([[460, 340], [690, 340]], seg(t, a1 + 2.0, a1 + 2.8), C.accent, 2, [6, 5]);
  K.lamp(80, 500, 280, 48, "PASSWORD CHOSEN", "GO", seg(t, a2 + 0.6, a2 + 1.0));
  text("Only a salted hash of it is kept.", 380, 531, { size: 17, color: C.soft, alpha: seg(t, a2 + 1.0, a2 + 1.5) });
}

function sLeft(t, d, cues) {
  K.head("GETTING STARTED", "WHAT IS LEFT TO DO", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.5), a1 = at(cues, 1, 3.6), a2 = at(cues, 2, 8);
  // the steps card of the incidents page; its rows sit at these fractions of the crop
  const x = 140, w = 1000, crop = [0.161, 0.114, 0.822, 0.328], rows = [0.156, 0.464, 0.770, 0.995];
  const h = browser(x, 160, w, "getting-started", seg(t, a0, a0 + 0.6), { crop }) - 30, top = 190;
  [0.05, 0.36, 0.68].forEach((f, i) => {
    const s = within(cues, 1, f, a1 + i, 0), k = seg(t, s, s + 0.4);
    if (k > 0) highlight(x + 8, top + h * rows[i] + 4, w - 16, h * (rows[i + 1] - rows[i]) - 8, k);
  });
  const kd = seg(t, a2 + 0.8, a2 + 1.3);
  ["SOURCE", "FIRST ALERT", "CHANNEL"].forEach((n, i) => K.lamp(140 + i * 200, 530, 180, 42, n, "GO", seg(t, a2 + 0.5 + i * 0.4, a2 + 0.8 + i * 0.4)));
  text("done: the list goes away", 760, 558, { size: 18, color: C.soft, alpha: kd });
}

function sSource(t, d, cues) {
  K.head("SOURCES", "A PROMETHEUS, ADDED HERE", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.5), a1 = at(cues, 1, 6.7), a2 = at(cues, 2, 8.4);
  const f = i => within(cues, 0, i, a0, a0 + 6);
  const kp = seg(t, a0, a0 + 0.5);
  panel(80, 170, 520, 400, { r: 4, fill: C.panel, stroke: C.line, alpha: kp });
  label("NEW PROMETHEUS SOURCE", 100, 202, kp);
  line([[80, 216], [600, 216]], C.line, 1, kp);
  field(104, 248, 470, "SERVER", "http://prometheus:9090", seg(t, f(0.1), f(0.2)), seg(t, f(0.2), f(0.4)));
  field(104, 330, 470, "TOKEN · READ-ONLY", "glsa_9k2m4n6p8q", seg(t, f(0.38), f(0.48)), seg(t, f(0.48), f(0.62)), { secret: true, lock: true });
  field(104, 412, 470, "METRICS AN INVESTIGATION MAY QUERY", "node_*, container_*, up", seg(t, f(0.66), f(0.76)), seg(t, f(0.76), f(0.98)));
  const kbtn = seg(t, a1, a1 + 0.3);
  if (kbtn > 0) {
    ctx.save(); ctx.globalAlpha *= kp; ctx.fillStyle = C.accent; ctx.fillRect(104, 500, 200, 44); ctx.restore();
    text("SAVE AND TEST", 204, 529, { size: 20, weight: 700, font: COND, color: C.deep, align: "center", spacing: "2px", alpha: kp });
    if (kbtn < 1) ring(204, 522, 20 + kbtn * 60, C.accent, 2, (1 - kbtn) * kp);
  }
  const row = (name, note, s) => ({ name, note, state: "GO", label: "GO", ks: seg(t, s, s + 0.3), kn: seg(t, s - 0.2, s + 0.3), nx: 170, size: 16, h: 64 });
  K.board(660, 170, 540, "PROM · PROMETHEUS", [
    row("SAVED", "secrets sealed with the key", a1 + 0.5),
    row("TESTED", "it answered a query", a2 + 0.3),
    row("CONNECTED", "tools offered, no restart", within(cues, 2, 0.6, a2 + 2, 0)),
  ], seg(t, 0.4, 1.0));
  const ke = seg(t, atEnd(cues, 2, 12) - 0.6, atEnd(cues, 2, 12));
  text("query_metrics_prom · alert_rules_prom · scrape_targets_prom", 660, 438, { size: 14, font: MONO, color: K.model, alpha: ke });
  text("for the next investigation", 660, 462, { size: 15, color: C.soft, alpha: ke });
}

function sChannel(t, d, cues) {
  K.head("CHANNELS", "WHERE PAEON TELLS YOU", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.5), a1 = at(cues, 1, 4.6), a2 = at(cues, 2, 9.4);
  const services = ["NTFY", "TELEGRAM", "E-MAIL", "+ 8 MORE"];
  services.forEach((s, i) => {
    const k = seg(t, within(cues, 0, 0.3 + i * 0.15, a0 + i, 0), within(cues, 0, 0.3 + i * 0.15, a0 + i, 0) + 0.4);
    K.lamp(80 + i * 170, 172, 156, 42, s, i === 1 ? "HOLD" : "", Math.max(k, 0));
  });
  const rows = [
    { name: "DIGEST", note: "once a day", state: "GO", label: "CARRIES", ks: seg(t, within(cues, 1, 0.35, a1 + 1, 0), within(cues, 1, 0.35, a1 + 1, 0) + 0.3) },
    { name: "ANALYSIS", note: "when an investigation ends", state: "GO", label: "CARRIES", ks: seg(t, within(cues, 1, 0.55, a1 + 2, 0), within(cues, 1, 0.55, a1 + 2, 0) + 0.3) },
    { name: "APPROVALS", note: "the executor's file only", state: "NO-GO", label: "NEVER", ks: seg(t, a2 + 0.6, a2 + 1.0), kn: seg(t, a2 + 0.8, a2 + 1.3) },
  ].map(r => ({ ...r, nx: 180, size: 16, h: 66 }));
  K.board(80, 250, 680, "FAMILY · TELEGRAM", rows, seg(t, a1, a1 + 0.5));
  K.phone(860, 150, 300, 430, {
    k: seg(t, within(cues, 1, 0.85, a1 + 4, 0), within(cues, 1, 0.85, a1 + 4, 0) + 0.6), clock: "18:42",
    title: "Paeon test",
    lines: ["This is a test from the Paeon", "console. This channel works."],
  }, seg(t, a1, a1 + 0.5));
}

function sKept(t, d, cues) {
  K.head("KEPT APART", "SECRETS, AND THE FILE", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.5), a1 = at(cues, 1, 5), a2 = at(cues, 2, 8.6);
  const kp = seg(t, a0, a0 + 0.5);
  panel(80, 170, 540, 330, { r: 4, fill: C.panel, stroke: C.line, alpha: kp });
  label("FAMILY · TELEGRAM", 100, 202, kp);
  line([[80, 216], [620, 216]], C.line, 1, kp);
  const kk = seg(t, within(cues, 0, 0.5, a0 + 2, 0), within(cues, 0, 0.5, a0 + 2, 0) + 0.5);
  field(104, 248, 490, "BOT TOKEN · KEPT", "Kept. Type a new one to replace it.", kp, kk, { ink: C.faint, lock: true, lockColor: C.good });
  const km = seg(t, a1, a1 + 0.6);
  field(104, 330, 490, "BOT API SERVER", km > 0 ? "https://collector.example.net" : "https://api.telegram.org", kp, km > 0 ? km : 1, { color: km > 0 ? C.accent : C.line });
  K.lamp(104, 420, 250, 46, "TYPE IT AGAIN", "HOLD", seg(t, a1 + 1.2, a1 + 1.6));
  text("a stored secret never follows", 372, 441, { size: 15, color: C.soft, alpha: seg(t, a1 + 1.6, a1 + 2.0) });
  text("to a new address", 372, 461, { size: 15, color: C.soft, alpha: seg(t, a1 + 1.6, a1 + 2.0) });
  // the file and the console, side by side
  const kb = seg(t, a2, a2 + 0.5), kw = seg(t, within(cues, 2, 0.7, a2 + 4, 0), within(cues, 2, 0.7, a2 + 4, 0) + 0.4);
  K.board(680, 170, 520, "CHANNELS", [
    { name: "PHONE", note: "rules file · locked", voice: "code", state: "GO", label: "THE FILE", ks: kw, nx: 150, size: 16, h: 64 },
    { name: "FAMILY", note: "added in the console", voice: "you", state: "", label: "", ks: 0, nx: 150, size: 16, h: 64 },
  ], kb);
  K.lock(1186 - 150, 254, 16, C.faint, kb);
  text("Never written. It wins on a name both define.", 680, 360, { size: 17, color: C.soft, alpha: kw });
}

function sRoles(t, d, cues) {
  K.head("BEHIND YOUR PROXY", "THREE ROLES, FROM ITS GROUPS", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.5), a1 = at(cues, 1, 3.5);
  const ka = seg(t, a0, a0 + 0.5);
  K.box(80, 170, 330, 90, "YOUR PROXY", "X-authentik-groups: paeon-admins", { k: ka, bar: C.line });
  K.wire([[245, 262], [245, 300]], seg(t, a0 + 0.8, a0 + 1.4), C.soft);
  const s = i => within(cues, 1, [0.02, 0.18, 0.62][i], a1 + i * 2, 0);
  K.board(80, 310, 1120, "WHO MAY DO WHAT", [
    { name: "VIEWER", note: "reads every page", state: "GO", label: "READS", ks: seg(t, s(0), s(0) + 0.3), kn: seg(t, s(0) - 0.2, s(0) + 0.3), nx: 220, h: 62 },
    { name: "OPERATOR", note: "also rates triage and stops automation", state: "HOLD", label: "ACTS ON", ks: seg(t, s(1), s(1) + 0.3), kn: seg(t, s(1) - 0.2, s(1) + 0.3), nx: 220, h: 62 },
    { name: "ADMIN", note: "also adds, tests and removes sources and channels", voice: "you", state: "GO", label: "CHANGES", ks: seg(t, s(2), s(2) + 0.3), kn: seg(t, s(2) - 0.2, s(2) + 0.3), nx: 220, h: 62 },
  ], seg(t, a0 + 1.0, a0 + 1.5));
  text("No proxy? One password, and whoever has it is the admin.", 80, 572, { size: 17, color: C.soft, alpha: seg(t, atEnd(cues, 1, 10) - 0.2, atEnd(cues, 1, 10) + 0.4) });
}

function sOutro(t, d, cues) {
  const a0 = at(cues, 0, 0.5), a1 = at(cues, 1, 2.2), kb = seg(t, 0.2, 0.8);
  const l1 = seg(t, a0, a0 + 0.6), l2 = seg(t, a1, a1 + 0.6), l3 = seg(t, within(cues, 1, 0.55, a1 + 1.5, 0), within(cues, 1, 0.55, a1 + 1.5, 0) + 0.6);
  K.lamp(440, 110, 180, 44, "BROWSER", "HOLD", seg(t, a0, a0 + 0.4));
  K.wire([[628, 132], [652, 132]], seg(t, a1, a1 + 0.4), C.soft);
  K.lamp(660, 110, 180, 44, "GIT", "GO", seg(t, a1 + 0.3, a1 + 0.7));
  text("START IN THE BROWSER.", 640, 290 + (1 - outCubic(l1)) * 14, { size: 60, weight: 700, font: COND, align: "center", spacing: "2px", alpha: l1 });
  text("MOVE TO GIT WHEN YOU WANT.", 640, 358 + (1 - outCubic(l2)) * 14, { size: 60, weight: 700, font: COND, align: "center", spacing: "2px", alpha: l2 });
  text("THE FILE ALWAYS WINS.", 640, 426 + (1 - outCubic(l3)) * 14, { size: 60, weight: 700, font: COND, align: "center", spacing: "2px", color: C.accent, alpha: l3 });
  ctx.save(); ctx.globalAlpha *= kb; ctx.strokeStyle = C.line; ctx.lineWidth = 1.5; ctx.strokeRect(410, 478, 460, 40); ctx.restore();
  text(S.status, 640, 505, { size: 19, weight: 600, font: COND, spacing: "3px", color: C.soft, align: "center", alpha: kb });
  text(S.url, 640, 568, { size: 18, font: MONO, color: C.accent, align: "center", alpha: seg(t, a1 + 1.0, a1 + 1.6) });
}

K.load(["sources", "setup", "getting-started"]);
VIDEO.create({
  title: { en: "Set up in the browser" },
  kicker: { en: "Paeon · 0.4" },
  langs: ["en"],
  theme: K.theme,
  fonts: K.fonts,
  strings: STR,
  background: K.bg,
  poster: "outro",
  scenes: {
    two: sTwo, start: sStart, code: sCode, left: sLeft, source: sSource,
    channel: sChannel, kept: sKept, roles: sRoles, outro: sOutro,
  },
});
K.finish();
