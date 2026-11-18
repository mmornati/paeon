/* One incident, from alert to fix. Scenes are pure functions of t, hooked onto the
 * narration's sentences with at(cues, i). The look comes from ../kit.js. Every name,
 * time and finding here is fictional. */
"use strict";

const STR = {
  en: {
    url: "mmornati.github.io/paeon",
    tagline: "MODELS THINK.  YOU SAY GO.  CODE DOES.",
  },
};

/* The board on the right of most scenes: how far the incident has come. */
const STATIONS = ["RECORD", "CORRELATE", "TRIAGE", "INVESTIGATE", "YOU", "EXECUTOR"];
function progress(lit, k = 1, o = {}) {
  // lit: {NAME: [state, ks]} ; anything else is on standby
  K.board(o.x ?? 800, o.y ?? 170, o.w ?? 400, "INCIDENT #3 · STATUS", STATIONS.map(name => {
    const s = lit[name];
    return { name, state: s ? s[0] : "", ks: s ? s[1] : 0, label: s && s[2] };
  }), k);
}
const GO = k => ["GO", k];

function sNight(t, d, cues) {
  K.head("21:04 · SERVER-1", "THE MEDIA SERVICE STOPS ANSWERING", seg(t, 0.2, 0.9));
  const a1 = at(cues, 1, 3.7), kb = seg(t, 0.5, 1.2), down = seg(t, a1 + 0.4, a1 + 1.0);
  // the host and what runs on it
  panel(80, 180, 700, 400, { r: 4, fill: C.panel, stroke: C.line, alpha: kb });
  text("SERVER-1 · DOCKER", 98, 209, { size: 15, weight: 600, font: COND, color: C.faint, spacing: "3px", alpha: kb });
  K.box(110, 240, 290, 76, "TRAEFIK", "reverse proxy", { k: kb });
  K.box(450, 240, 290, 76, "MEDIA-SERVER", down > 0 ? "unhealthy · answers nothing" : "healthy", { k: kb, color: down > 0 ? C.bad : C.line, bar: down > 0 ? C.bad : C.good, subColor: down > 0 ? C.bad : C.faint });
  K.box(450, 356, 290, 76, "MEDIA-DB", "restarted at 20:30 for the backup", { k: kb, bar: C.good });
  K.box(110, 356, 290, 76, "WORKER", "healthy", { k: kb, bar: C.good });
  K.wire([[400, 278], [450, 278]], kb, C.line);
  K.wire([[595, 356], [595, 316]], kb, C.line);
  // a request that no longer gets an answer
  text("media.example", 110, 500, { size: 16, font: MONO, color: C.soft, alpha: kb });
  K.wire([[255, 494], [255, 470], [255, 432]], 0, C.line);
  text("GET /", 110, 530, { size: 15, font: MONO, color: C.faint, alpha: kb });
  text(down > 0 ? "502 Bad Gateway" : "200 OK", 180, 530, { size: 15, font: MONO, weight: 600, color: down > 0 ? C.bad : C.good, alpha: kb });
  if (down > 0) cross(732, 248, 9, C.bad, down);
  // the clock, and a phone that is still asleep
  text("21:04", 1200, 330, { size: 150, weight: 700, font: COND, align: "right", color: C.ink, alpha: seg(t, 0.6, 1.4), spacing: "2px" });
  text("WEDNESDAY EVENING", 1200, 372, { size: 18, weight: 600, font: COND, align: "right", color: C.faint, spacing: "3px", alpha: seg(t, 0.8, 1.6) });
  text("Nobody is watching a dashboard.", 1200, 560, { size: 18, color: C.soft, align: "right", alpha: seg(t, a1 + 1.4, a1 + 2.0) });
}

function sSources(t, d, cues) {
  K.head("TWO SOURCES", "THE ALERTS FIRE", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.5), a1 = at(cues, 1, 6.3), a2 = at(cues, 2, 10.8);
  const k0 = seg(t, a0 + 0.3, a0 + 0.9), k1 = seg(t, a1, a1 + 0.6);
  K.box(80, 190, 400, 96, "OPENOBSERVE", "slo_traefik_5xx · warning", { k: k0, bar: C.warn, subColor: C.soft });
  text("error budget burn on Traefik", 102, 310, { size: 15, color: C.faint, alpha: k0 });
  K.box(80, 380, 400, 96, "UPTIME-KUMA", "media is DOWN · critical", { k: k1, bar: C.bad, subColor: C.soft });
  text("the monitor gets no answer", 102, 500, { size: 15, color: C.faint, alpha: k1 });
  // the usual path
  const kn = seg(t, a2 - 0.2, a2 + 0.4);
  K.box(800, 190, 400, 96, "YOUR NOTIFICATIONS", "as always, untouched", { k: kn });
  K.wire([[480, 238], [800, 238]], seg(t, a2, a2 + 0.7), C.soft);
  K.wire([[480, 428], [640, 428], [640, 262], [800, 262]], seg(t, a2 + 0.2, a2 + 0.9), C.soft);
  // the copy
  const kc = seg(t, a2 + 3.0, a2 + 3.6);
  K.box(800, 380, 400, 96, "PAEON", "gets a copy · never in the path", { k: kc, color: C.accent, bar: C.accent, subColor: C.soft });
  K.wire([[480, 262], [560, 262], [560, 404], [800, 404]], seg(t, a2 + 3.2, a2 + 4.0), C.accent, 2, [7, 6]);
  K.wire([[480, 452], [800, 452]], seg(t, a2 + 3.4, a2 + 4.2), C.accent, 2, [7, 6]);
  text("polled, with a read-only account", 560, 222, { size: 13, font: MONO, color: C.faint, alpha: seg(t, a2 + 4.2, a2 + 4.8) });
  text("webhook, with a bearer token", 560, 478, { size: 13, font: MONO, color: C.faint, alpha: seg(t, a2 + 4.4, a2 + 5.0) });
}

function sIncident(t, d, cues) {
  K.head("RECORD · CORRELATE", "TWO ALERTS, ONE INCIDENT", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.5), a1 = at(cues, 1, 3.6), a2 = at(cues, 2, 8.5);
  panel(80, 170, 680, 250, { r: 4, fill: C.panel, stroke: C.line, alpha: seg(t, 0.3, 0.9) });
  text("EVENT LOG · APPEND-ONLY", 98, 199, { size: 15, weight: 600, font: COND, color: C.faint, spacing: "3px", alpha: seg(t, 0.3, 0.9) });
  K.log(98, 250, 644, [
    { time: "21:04:02", tag: "in", text: "slo_traefik_5xx is firing", kind: "alert.firing", a: a0 + 0.3 },
    { time: "21:04:02", tag: "code", text: "Incident opened", kind: "incident.opened", a: a0 + 1.0 },
    { time: "21:04:32", tag: "in", text: "media is DOWN is firing", kind: "alert.firing", a: a0 + 1.7 },
    { time: "21:04:32", tag: "code", text: "media is DOWN joined (same host)", kind: "incident.correlated", a: a1 + 0.6 },
  ], t);
  // the incident
  const ki = seg(t, a1 + 1.4, a1 + 2.0);
  panel(80, 450, 680, 110, { r: 4, fill: C.panel2, stroke: C.accent, lw: 1.5, alpha: ki });
  text("#3", 104, 500, { size: 34, weight: 700, font: COND, color: C.accent, alpha: ki });
  text("slo_traefik_5xx", 160, 494, { size: 24, weight: 600, alpha: ki });
  text("server-1 · warning · 2 of 2 alerts firing", 160, 526, { size: 16, color: C.soft, alpha: ki });
  progress({ RECORD: GO(seg(t, a0 + 0.6, a0 + 0.9)), CORRELATE: GO(seg(t, a1 + 1.6, a1 + 1.9)) }, seg(t, 0.4, 1.0));
  text("No model called so far.", 800, 580, { size: 17, color: C.soft, alpha: seg(t, a2 + 0.2, a2 + 0.8) });
}

function sTriage(t, d, cues) {
  K.head("TRIAGE · ONE CALL, NO TOOLS", "NOISE, OR REAL?", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.5), a1 = at(cues, 1, 4.2), a2 = at(cues, 2, 8.1), a3 = at(cues, 3, 11.4);
  const k0 = seg(t, a0 + 0.2, a0 + 0.8);
  K.box(80, 180, 300, 96, "CONTEXT PACK", "built by code: titles, labels, history", { k: k0 });
  K.wire([[380, 228], [450, 228]], seg(t, a0 + 0.8, a0 + 1.4), C.soft);
  K.box(450, 180, 300, 96, "CHEAP MODEL", "one call · no tools", { k: seg(t, a0 + 1.0, a0 + 1.6), color: K.model, bar: K.model });
  // the four verdicts
  const verdicts = ["NOISE", "TRANSIENT", "KNOWN ISSUE", "REAL"];
  verdicts.forEach((v, i) => {
    const k = seg(t, a1 + i * 0.55, a1 + 0.4 + i * 0.55), x = 80 + i * 170, lit = v === "REAL" ? seg(t, a2 + 0.3, a2 + 0.7) : 0;
    ctx.save(); ctx.globalAlpha *= k;
    if (lit > 0) { ctx.fillStyle = K.model; ctx.globalAlpha *= lit; ctx.fillRect(x, 330, 160, 56); }
    ctx.restore();
    ctx.save(); ctx.globalAlpha *= k; ctx.strokeStyle = lit > 0 ? K.model : C.line; ctx.lineWidth = 2; ctx.strokeRect(x + 1, 331, 158, 54); ctx.restore();
    text(v, x + 80, 367, { size: 23, weight: 600, font: COND, spacing: "2px", align: "center", color: lit > 0.5 ? C.deep : C.soft, alpha: k });
  });
  text("86% sure", 670, 420, { size: 20, weight: 600, color: K.model, align: "center", alpha: seg(t, a2 + 0.9, a2 + 1.4) });
  text("Shadow mode: the verdict is recorded, nothing acts on it.", 80, 420, { size: 15, color: C.faint, alpha: seg(t, a2 + 1.6, a2 + 2.2) });
  // fails open
  const kf = seg(t, a3, a3 + 0.6);
  line([[80, 470], [750, 470]], C.line, 1, kf);
  ["model down", "invalid answer", "low confidence", "budget spent"].forEach((s, i) =>
    text(s, 80 + i * 170, 508, { size: 16, color: C.soft, alpha: seg(t, a3 + 0.3 + i * 0.35, a3 + 0.7 + i * 0.35) }));
  K.lamp(80, 530, 280, 44, "TREATED AS REAL", "HOLD", seg(t, a3 + 3.4, a3 + 3.8));
  text("Triage fails open.", 380, 559, { size: 18, weight: 600, alpha: seg(t, a3 + 4.6, a3 + 5.1) });
  progress({ RECORD: GO(1), CORRELATE: GO(1), TRIAGE: GO(seg(t, a2 + 0.6, a2 + 0.9)) });
}

function sInvestigate(t, d, cues) {
  K.head("INVESTIGATION · READ-ONLY", "A STRONG MODEL LOOKS", seg(t, 0.2, 0.9));
  const a = [0, 1, 2, 3, 4].map(i => at(cues, i, [0.5, 5.2, 8.5, 11.7, 15.1][i]));
  K.box(80, 180, 680, 70, "STRONG MODEL", "", { k: seg(t, a[0] + 0.2, a[0] + 0.8), color: K.model, bar: K.model });
  text("reads, hands in findings, changes nothing", 310, 223, { size: 15, color: C.faint, alpha: seg(t, a[0] + 0.6, a[0] + 1.2) });
  const calls = [
    ["c1", "search_logs", "OPENOBSERVE", "502 for media.example from 21:03:58"],
    ["c2", "inspect_container", "DOCKER PROXY · GET ONLY", "running, but unhealthy"],
    ["c3", "container_logs", "DOCKER PROXY · GET ONLY", "\"connection pool exhausted\" since 20:31"],
  ];
  calls.forEach(([id, tool, where, got], i) => {
    const y = 278 + i * 96, s = a[i + 1], k = seg(t, s, s + 0.5);
    if (k <= 0) return;
    line([[80, y + 84], [760, y + 84]], C.line, 1, k);
    ctx.save(); ctx.globalAlpha *= k; ctx.strokeStyle = K.model; ctx.lineWidth = 1.5; rr(80, y + 8, 46, 28, 14); ctx.stroke(); ctx.restore();
    text(id, 103, y + 28, { size: 15, font: MONO, weight: 600, color: K.model, align: "center", alpha: k });
    text(tool, 142, y + 29, { size: 18, font: MONO, color: C.ink, alpha: k });
    K.lock(446, y + 24, 20, C.accent, k);
    text(where, 466, y + 29, { size: 17, weight: 600, font: COND, spacing: "2px", color: C.soft, alpha: k });
    text(typed(got, seg(t, s + 0.7, s + 1.7)), 142, y + 62, { size: 16, color: K.model, alpha: k });
  });
  const kl = seg(t, a[4], a[4] + 0.6);
  line([[66, 286], [66, 556]], C.accent, 2, kl);
  ctx.save(); ctx.translate(50, 421); ctx.rotate(-Math.PI / 2);
  text("YOUR ALLOW-LIST", 0, 0, { size: 15, weight: 600, font: COND, spacing: "3px", color: C.accent, align: "center", alpha: kl });
  ctx.restore();
  progress({ RECORD: GO(1), CORRELATE: GO(1), TRIAGE: GO(1), INVESTIGATE: ["RUN", seg(t, a[0] + 0.8, a[0] + 1.1), "READING"] });
}

/* Where a point of the investigation screenshot lands on the canvas. */
const SHOT = { x: 80, y: 168, w: 720, crop: [0.268, 0.066, 0.632, 0.56] };
const onShot = (px, py) => [SHOT.x + (px / 1920 - SHOT.crop[0]) / SHOT.crop[2] * SHOT.w,
                            SHOT.y + (py / 1200 - SHOT.crop[1]) / SHOT.crop[3] * (SHOT.w * (1200 * SHOT.crop[3]) / (1920 * SHOT.crop[2]))];

function sAnalysis(t, d, cues) {
  K.head("THE ANALYSIS", "A ROOT CAUSE, WITH EVIDENCE", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.5), a1 = at(cues, 1, 3.6), a2 = at(cues, 2, 10.4), a3 = at(cues, 3, 13.7);
  K.shot("investigation", SHOT.x, SHOT.y, SHOT.w, { k: seg(t, a0 + 0.2, a0 + 0.9), crop: SHOT.crop });
  text("The console, on this incident", 80, 594, { size: 13, font: MONO, color: C.faint, alpha: seg(t, a0 + 0.6, a0 + 1.2) });
  // what to look at
  const k1 = seg(t, a1, a1 + 0.6);
  text("ROOT CAUSE", 840, 200, { size: 17, weight: 600, font: COND, spacing: "3px", color: K.model, alpha: k1 });
  para("media-server lost its database connections during the backup, and does not reconnect by itself.", 840, 232, 360, { size: 19, lh: 28, color: C.ink, alpha: k1 });
  text("78% sure · 3 tool calls · 2 min", 840, 348, { size: 15, font: MONO, color: C.faint, alpha: seg(t, a1 + 1.2, a1 + 1.8) });
  const [ex, ey] = onShot(1630, 512), [ex2, ey2] = onShot(1678, 664);
  const k2 = seg(t, a2, a2 + 0.6);
  highlight(ex, ey, ex2 - ex, ey2 - ey, k2, C.accent);
  line(partial([[ex2 + 8, (ey + ey2) / 2], [840, 420]], k2), C.accent, 1.5, 0.9);
  text("EVERY CLAIM → ITS TOOL CALL", 840, 412, { size: 17, weight: 600, font: COND, spacing: "3px", color: C.accent, alpha: k2 });
  text("c1, c2, c3 open the call in the record", 840, 440, { size: 15, color: C.soft, alpha: seg(t, a2 + 0.5, a2 + 1.1) });
  const [nx, ny] = onShot(1474, 682), [nx2, ny2] = onShot(1680, 718);
  const k3 = seg(t, a3, a3 + 0.6);
  highlight(nx, ny, nx2 - nx, ny2 - ny, k3, C.bad);
  line(partial([[nx2 + 8, (ny + ny2) / 2], [840, 510]], k3), C.bad, 1.5, 0.9);
  text("NOTHING BEHIND IT: MARKED", 840, 502, { size: 17, weight: 600, font: COND, spacing: "3px", color: C.bad, alpha: k3 });
  text("a claim that cites no real call says so", 840, 530, { size: 15, color: C.soft, alpha: seg(t, a3 + 0.5, a3 + 1.1) });
}

function sPlan(t, d, cues) {
  K.head("THE PLAN", "ONE STEP IS IN THE CATALOG", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.5), a1 = at(cues, 1, 3.1), a2 = at(cues, 2, 7.3), a3 = at(cues, 3, 9.7);
  const k0 = seg(t, a0 + 0.2, a0 + 0.8), in1 = seg(t, a1 + 0.8, a1 + 1.3), out2 = seg(t, a2, a2 + 0.6);
  text("SUGGESTED BY THE MODEL", 80, 196, { size: 15, weight: 600, font: COND, color: K.model, spacing: "3px", alpha: k0 });
  panel(80, 212, 640, 74, { r: 4, fill: C.panel, stroke: in1 > 0 ? C.good : C.line, lw: in1 > 0 ? 2 : 1, alpha: k0 });
  text("1", 104, 258, { size: 26, weight: 700, font: COND, color: C.faint, alpha: k0 });
  text("Restart media-server.", 136, 246, { size: 19, color: K.model, weight: 500, alpha: k0 });
  text("restart_container(media-server)", 136, 272, { size: 15, font: MONO, color: C.ink, alpha: seg(t, a1, a1 + 0.5) });
  K.lamp(560, 228, 146, 42, "IN CATALOG", "GO", in1);
  panel(80, 298, 640, 74, { r: 4, fill: C.panel, stroke: C.line, alpha: k0 * (1 - out2 * 0.45) });
  text("2", 104, 344, { size: 26, weight: 700, font: COND, color: C.faint, alpha: k0 * (1 - out2 * 0.45) });
  text("Make media-server reconnect after a database restart.", 136, 332, { size: 19, color: K.model, weight: 500, alpha: k0 * (1 - out2 * 0.45) });
  text("a sentence, for you", 136, 358, { size: 15, color: C.accent, alpha: out2 });
  // the closed catalog
  const kc = seg(t, a1 - 0.2, a1 + 0.4);
  panel(800, 170, 400, 262, { r: 4, fill: C.panel, stroke: C.line, alpha: kc });
  text("THE CATALOG · CLOSED", 818, 199, { size: 15, weight: 600, font: COND, color: C.faint, spacing: "3px", alpha: kc });
  ["restart_container", "start_container", "unpause_container", "reattach_network_alias", "coolify_restart", "config_change"].forEach((s, i) => {
    const on = i === 0 ? in1 : 0;
    text(s, 836, 238 + i * 32, { size: 16, font: MONO, color: on > 0.5 ? C.good : C.soft, weight: on > 0.5 ? 600 : 400, alpha: kc });
    if (on > 0) dot(822, 233 + i * 32, 4, C.good, on);
  });
  // two processes
  const k3 = seg(t, a3, a3 + 0.6);
  K.box(80, 462, 380, 96, "THE CORE", "reads alerts, talks to models, cannot act", { k: k3 });
  K.box(820, 462, 380, 96, "THE EXECUTOR", "checks the plan against its own rules", { k: seg(t, a3 + 1.2, a3 + 1.8), color: C.good, bar: C.good });
  line([[640, 440], [640, 580]], C.accent, 2, k3, [8, 7]);
  text("TWO PROCESSES", 640, 432, { size: 14, weight: 600, font: COND, spacing: "3px", color: C.accent, align: "center", alpha: k3 });
  const kp = inOut(seg(t, a3 + 1.6, a3 + 3.2));
  K.wire([[460, 510], [820, 510]], seg(t, a3 + 1.0, a3 + 1.8), C.soft);
  if (kp > 0 && kp < 1) pill("plan #11", lerp(520, 760, kp), 510, { align: "center", fill: C.deep, stroke: C.accent, color: C.accent, size: 13 });
  check(1172, 484, 10, C.good, seg(t, a3 + 3.3, a3 + 3.8));
}

function sPhone(t, d, cues) {
  K.head("LEVEL: APPROVE", "THE EXECUTOR ASKS YOU", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.5), a1 = at(cues, 1, 3.5), a2 = at(cues, 2, 6.5), a3 = at(cues, 3, 8.9);
  const k0 = seg(t, a0 + 0.2, a0 + 0.8);
  text("YOUR POLICY", 80, 196, { size: 15, weight: 600, font: COND, color: C.faint, spacing: "3px", alpha: k0 });
  panel(80, 210, 640, 52, { r: 4, fill: C.deep, stroke: C.line, alpha: k0 });
  text('- { alert: "slo_.*", level: approve }', 100, 243, { size: 17, font: MONO, color: C.termInk, alpha: k0 });
  ["OBSERVE", "SUGGEST", "APPROVE", "AUTO"].forEach((l, i) => {
    const x = 80 + i * 162, on = l === "APPROVE";
    K.lamp(x, 286, 154, 46, l, on ? "HOLD" : "", seg(t, a0 + 0.6 + i * 0.2, a0 + 1.0 + i * 0.2) * (on ? 1 : 0.8));
  });
  text("records", 157, 356, { size: 14, color: C.faint, align: "center", alpha: seg(t, a0 + 1.6, a0 + 2.2) });
  text("tells you", 319, 356, { size: 14, color: C.faint, align: "center", alpha: seg(t, a0 + 1.6, a0 + 2.2) });
  text("waits for your Go", 481, 356, { size: 14, color: C.accent, align: "center", alpha: seg(t, a0 + 1.6, a0 + 2.2) });
  text("runs, then tells you", 643, 356, { size: 14, color: C.faint, align: "center", alpha: seg(t, a0 + 1.6, a0 + 2.2) });
  // the phone
  K.phone(860, 150, 300, 440, {
    k: seg(t, a1 + 0.9, a1 + 1.5), clock: "21:08",
    title: "Incident #3: run this fix?",
    lines: ["slo_traefik_5xx", "1. Restart the container", "    (media-server)", "Nothing runs without Go."],
    buttons: [{ label: "GO", kind: "go" }, { label: "NO-GO", kind: "no" }],
  }, seg(t, a1 - 0.3, a1 + 0.4));
  // what the button is
  const facts = [["SIGNED", "by the executor: the core never sees the link"], ["ONCE", "a second tap does nothing"], ["30 MIN", "then it expires, and nothing runs"], ["THIS PLAN", "a new plan needs a new Go"]];
  facts.forEach(([l, s], i) => {
    const k = seg(t, a3 + i * 1.1, a3 + 0.5 + i * 1.1), y = 396 + i * 48;
    K.lamp(80, y, 130, 38, l, "", k);
    text(s, 228, y + 25, { size: 16, color: C.soft, alpha: k });
  });
}

function sGo(t, d, cues) {
  K.head("GO", "CHECK, APPLY, VERIFY", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.5), a1 = at(cues, 1, 2.0), a2 = at(cues, 2, 6.0);
  K.phone(80, 170, 250, 400, {
    clock: "21:09", title: "Incident #3: run this fix?", lines: ["1. Restart the container"],
    buttons: [{ label: "GO", kind: "go", pressed: seg(t, a0 + 0.2, a0 + 0.9) }, { label: "NO-GO", kind: "no" }],
  }, seg(t, 0.2, 0.8));
  K.guard(380, 280, seg(t, a0 + 0.3, a0 + 0.9), seg(t, a0 + 0.8, a0 + 1.4), seg(t, 0.3, 0.9));
  text("the executor's switch", 455, 498, { size: 14, color: C.faint, align: "center", alpha: seg(t, 0.5, 1.1) });
  K.board(580, 190, 620, "EXECUTOR · PLAN #11", [
    { name: "PRE-CHECK", note: "the world is as the plan assumed", state: "GO", ks: seg(t, a1 + 2.6, a1 + 2.9), kn: seg(t, a1, a1 + 0.6), nx: 180, size: 16, h: 64 },
    { name: "LOCK", note: "one lock per container", state: "GO", ks: seg(t, a1 + 3.3, a1 + 3.6), kn: seg(t, a1 + 2.9, a1 + 3.4), nx: 180, size: 16, h: 64 },
    { name: "APPLY", note: "restart_container(media-server)", state: "GO", ks: seg(t, a2 + 1.6, a2 + 1.9), kn: seg(t, a2, a2 + 0.6), nx: 180, size: 16, h: 64 },
    { name: "VERIFY", note: "running, and healthy", state: "GO", ks: seg(t, a2 + 4.2, a2 + 4.5), kn: seg(t, a2 + 2.2, a2 + 2.8), nx: 180, size: 16, h: 64 },
  ], seg(t, 0.4, 1.0));
  text("On failure: roll back where there is an inverse, stop, and hand the incident back to you.", 580, 540, { size: 15, color: C.faint, alpha: seg(t, a2 + 4.6, a2 + 5.2) });
}

function sResolved(t, d, cues) {
  K.head("RESOLVED", "THE INCIDENT CLOSES", seg(t, 0.2, 0.9));
  const a0 = at(cues, 0, 0.5), a1 = at(cues, 1, 3.6), a2 = at(cues, 2, 8.1);
  K.shot("plan-verified", 80, 170, 700, { k: seg(t, a0 + 0.2, a0 + 0.8), crop: [0.268, 0.066, 0.632, 0.285] });
  K.log(80, 440, 700, [
    { time: "21:11:40", tag: "in", text: "slo_traefik_5xx resolved", kind: "alert.resolved", a: a0 + 0.8 },
    { time: "21:12:10", tag: "in", text: "media is DOWN resolved", kind: "alert.resolved", a: a0 + 1.4 },
    { time: "21:12:10", tag: "code", text: "Incident resolved after 8 min", kind: "incident.resolved", a: a0 + 2.0 },
    { time: "21:42:10", tag: "sent", text: "Postmortem draft written (status: draft)", kind: "postmortem.drafted", a: a2 + 0.4 },
  ], t, { lh: 36 });
  K.phone(880, 150, 300, 440, {
    k: seg(t, a1 + 0.2, a1 + 0.8), clock: "21:10", title: "Incident #3: fix applied",
    lines: ["Ran after your Go:", "1. Restart the container", "    (media-server)", "Verified: up and healthy."],
    buttons: [{ label: "THIS WAS WRONG", kind: "plain" }],
  }, seg(t, 0.3, 0.9));
}

function sOutro(t, d, cues) {
  const a0 = at(cues, 0, 0.5), a1 = at(cues, 1, 4.7), kb = seg(t, 0.2, 0.8);
  const rows = [
    ["RECORD", "two alerts, as they came", "code"], ["CORRELATE", "one incident on server-1", "code"],
    ["TRIAGE", "real, 86% sure", "model"], ["INVESTIGATE", "a root cause, 78% sure, three tool calls", "model"],
    ["YOU", "Go", "you"], ["EXECUTOR", "restarted, verified healthy", "code"],
  ];
  K.board(80, 76, 1120, "INCIDENT #3 · EVERY STEP IS ONE LINE OF THE LOG", rows.map(([name, note, voice], i) => ({
    name, note, voice, state: "GO", ks: seg(t, a0 + 0.3 + i * 0.5, a0 + 0.6 + i * 0.5), kn: seg(t, a0 + 0.2 + i * 0.5, a0 + 0.7 + i * 0.5), nx: 230, size: 19,
  })), kb);
  const k1 = seg(t, a1, a1 + 0.7);
  text(S.tagline, 640, 530 + (1 - outCubic(k1)) * 14, { size: 56, weight: 700, font: COND, align: "center", spacing: "2px", alpha: k1 });
  text(S.url, 640, 578, { size: 18, font: MONO, color: C.accent, align: "center", alpha: seg(t, a1 + 2.6, a1 + 3.2) });
}

K.load(["investigation", "plan-verified"]);
VIDEO.create({
  title: { en: "One incident, from alert to fix" },
  kicker: { en: "Paeon · the flow" },
  langs: ["en"],
  theme: K.theme,
  fonts: K.fonts,
  strings: STR,
  background: K.bg,
  poster: "outro",
  scenes: {
    night: sNight, sources: sSources, incident: sIncident, triage: sTriage, investigate: sInvestigate,
    analysis: sAnalysis, plan: sPlan, phone: sPhone, go: sGo, resolved: sResolved, outro: sOutro,
  },
});
K.finish();
