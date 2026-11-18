/* Paeon video kit: the status-board look shared by every episode (loaded after engine.js,
 * before an episode's scenes.js). Everything here is a pure function of its arguments.
 * The world: a launch-day Go / No-go board. Green is GO, amber is HOLD, red is NO-GO,
 * violet is what a model said, chalk white is what code recorded. */
"use strict";

const COND = '"Barlow Condensed", Sora, sans-serif';

const K = {
  theme: {
    bg: "#0b1626", bg2: "#0d1a2d", panel: "#12233b", panel2: "#183052", line: "#2a4368",
    ink: "#f2f5f8", soft: "#b6c3d6", faint: "#8a9bb4", accent: "#ffb000", accent2: "#b9a0ff",
    good: "#2fd07b", warn: "#ffb000", bad: "#ff4d4f", deep: "#071020", term: "#071020", termInk: "#d7e2f0",
  },
  fonts: ['500 20px "Barlow Condensed"', '600 20px "Barlow Condensed"', '700 20px "Barlow Condensed"'],
  info: "#62adff",
  model: "#b9a0ff",

  /* The board's ground: flat navy, a header strip that names the show and says the data is made up. */
  bg(t) {
    ctx.fillStyle = C.bg; ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = C.deep; ctx.fillRect(0, 0, W, 46);
    ctx.fillStyle = C.accent; ctx.fillRect(80, 16, 14, 14);
    text("PAEON", 106, 31, { size: 22, weight: 700, font: COND, spacing: "4px" });
    text("FICTIONAL INCIDENT · SYNTHETIC DATA", 1200, 29, { size: 11, font: MONO, color: C.faint, align: "right", spacing: "1px" });
    line([[0, 46], [W, 46]], C.line, 1, 1);
  },

  /* Scene title: a small amber label and a condensed headline in capitals. */
  head(label, title, k, y = 128) {
    text(label, 80, y - 44, { size: 17, weight: 600, font: COND, color: C.accent, spacing: "3px", alpha: k });
    text(title, 80, y + (1 - outCubic(k)) * 14, { size: 50, weight: 700, font: COND, spacing: "1px", alpha: k });
  },

  /* A status cell: the lit rectangle at the end of a board row. */
  lamp(x, y, w, h, label, state, k = 1) {
    if (k <= 0) return;
    const fill = { GO: C.good, HOLD: C.accent, "NO-GO": C.bad, RUN: C.accent }[state];
    ctx.save();
    ctx.globalAlpha *= clamp(k);
    if (fill && state !== "RUN") {
      ctx.fillStyle = fill; ctx.fillRect(x, y, w, h);
      text(label, x + w / 2, y + h / 2 + 8, { size: 22, weight: 700, font: COND, color: C.deep, align: "center", spacing: "2px" });
    } else {
      ctx.strokeStyle = fill || C.line; ctx.lineWidth = 2; ctx.strokeRect(x + 1, y + 1, w - 2, h - 2);
      text(label, x + w / 2, y + h / 2 + 8, { size: 22, weight: 600, font: COND, color: fill || C.faint, align: "center", spacing: "2px" });
    }
    ctx.restore();
  },

  /* One row of the board. r = {name, note, voice: code|model|you, state, label, k, ks}:
   * the row fades in with k, its lamp lights with ks. */
  row(x, y, w, r) {
    const k = r.k ?? 1;
    if (k <= 0) return;
    const h = r.h ?? 54, lw = r.lw ?? 132;
    line([[x, y + h], [x + w, y + h]], C.line, 1, k);
    text(r.name, x + 18, y + h / 2 + 9, { size: 25, weight: 600, font: COND, spacing: "2px", alpha: k });
    const color = { model: K.model, you: C.accent, code: C.soft }[r.voice ?? "code"];
    if (r.note) text(r.note, x + (r.nx ?? 210), y + h / 2 + 6, { size: r.size ?? 17, color, alpha: k * (r.kn ?? 1), weight: r.voice === "model" ? 500 : 400 });
    const ks = r.ks ?? 0;
    if (ks > 0) K.lamp(x + w - lw - 10, y + 9, lw, h - 18, r.label ?? r.state, r.state, ks);
    else K.lamp(x + w - lw - 10, y + 9, lw, h - 18, "STANDBY", "", k * 0.7);
  },

  /* A whole board: a panel, a caption line, then its rows. */
  board(x, y, w, title, rows, k = 1) {
    const h = 44 + rows.reduce((s, r) => s + (r.h ?? 54), 0) + 6;
    panel(x, y, w, h, { r: 4, fill: C.panel, stroke: C.line, alpha: k });
    text(title, x + 18, y + 29, { size: 15, weight: 600, font: COND, color: C.faint, spacing: "3px", alpha: k });
    line([[x, y + 44], [x + w, y + 44]], C.line, 1, k);
    let cy = y + 44;
    for (const r of rows) { K.row(x, cy, w, { ...r, k: (r.k ?? 1) * k }); cy += r.h ?? 54; }
    return h;
  },

  /* A station on a diagram: a square-cornered box with a name and one line under it. */
  box(x, y, w, h, name, sub, o = {}) {
    const k = o.k ?? 1;
    if (k <= 0) return;
    panel(x, y, w, h, { r: 4, fill: o.fill ?? C.panel, stroke: o.color ?? C.line, lw: o.lw ?? 1.5, alpha: k });
    if (o.bar) { ctx.save(); ctx.globalAlpha *= k; ctx.fillStyle = o.bar; ctx.fillRect(x, y, 6, h); ctx.restore(); }
    text(name, x + 22, y + (sub ? h / 2 - 2 : h / 2 + 8), { size: o.size ?? 24, weight: 600, font: COND, spacing: "1.5px", alpha: k, color: o.ink ?? C.ink });
    if (sub) text(sub, x + 22, y + h / 2 + 22, { size: 14, color: o.subColor ?? C.faint, alpha: k });
  },

  /* A wire that draws itself, with an arrow head once it has arrived. */
  wire(points, k, color = C.line, lw = 2, dash) {
    if (k <= 0) return;
    line(partial(points, outCubic(clamp(k))), color, lw, 1, dash);
    if (k >= 1) {
      const [a, b] = points.slice(-2), an = Math.atan2(b[1] - a[1], b[0] - a[0]);
      ctx.save(); ctx.fillStyle = color; ctx.translate(b[0], b[1]); ctx.rotate(an);
      ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(-11, -5.5); ctx.lineTo(-11, 5.5); ctx.closePath(); ctx.fill(); ctx.restore();
    }
  },

  /* Lines of the event log. rows = [{time, tag, text, a}]; a row appears at local time a. */
  log(x, y, w, rows, t, o = {}) {
    const lh = o.lh ?? 38, size = o.size ?? 17;
    const tags = { in: K.info, code: C.soft, model: K.model, tool: K.model, you: C.accent, sent: C.good, exec: C.good };
    rows.forEach((r, i) => {
      const k = seg(t, r.a, r.a + 0.35);
      if (k <= 0) return;
      const ry = y + i * lh;
      text(r.time, x, ry, { size: 14, font: MONO, color: C.faint, alpha: k });
      const c = tags[r.tag] ?? C.soft;
      ctx.save(); ctx.globalAlpha *= k; ctx.strokeStyle = c; ctx.lineWidth = 1.5; ctx.strokeRect(x + 86, ry - 17, 64, 23); ctx.restore();
      text(r.tag.toUpperCase(), x + 118, ry - 0.5, { size: 14, weight: 600, font: COND, color: c, align: "center", spacing: "1.5px", alpha: k });
      text(typed(r.text, seg(t, r.a, r.a + 0.6)), x + 166, ry, { size, color: r.tag === "model" || r.tag === "tool" ? K.model : C.ink, alpha: k });
      if (r.kind) text(r.kind, x + w, ry, { size: 13, font: MONO, color: C.faint, align: "right", alpha: k });
    });
  },

  /* A phone holding one notification. n = {title, lines, buttons: [{label, kind, pressed}], k}. */
  phone(x, y, w, h, n = {}, k = 1) {
    if (k <= 0) return;
    panel(x, y, w, h, { r: 34, fill: "#05080f", stroke: C.line, lw: 2, alpha: k, shadow: true });
    panel(x + 10, y + 10, w - 20, h - 20, { r: 26, fill: C.deep, stroke: false, alpha: k });
    panel(x + w / 2 - 38, y + 20, 76, 18, { r: 9, fill: "#05080f", stroke: false, alpha: k });
    text(n.clock ?? "21:08", x + w / 2, y + 96, { size: 46, weight: 500, font: COND, align: "center", color: C.soft, alpha: k, spacing: "2px" });
    const kn = n.k ?? 1;
    if (kn <= 0) return;
    const nx = x + 22, nw = w - 44, lines = n.lines ?? [], nh = 78 + lines.length * 22 + (n.buttons ? 54 : 0);
    const ny = y + 128 + (1 - outCubic(kn)) * 26;
    panel(nx, ny, nw, nh, { r: 14, fill: C.panel2, stroke: C.line, alpha: k * kn });
    ctx.save(); ctx.globalAlpha *= k * kn; ctx.fillStyle = C.accent; ctx.fillRect(nx + 14, ny + 15, 10, 10); ctx.restore();
    text("PAEON", nx + 32, ny + 25, { size: 14, weight: 700, font: COND, spacing: "2px", color: C.soft, alpha: k * kn });
    text(n.when ?? "now", nx + nw - 14, ny + 24, { size: 12, color: C.faint, align: "right", alpha: k * kn });
    text(n.title ?? "", nx + 14, ny + 54, { size: 17, weight: 700, alpha: k * kn });
    lines.forEach((l, i) => text(l, nx + 14, ny + 80 + i * 22, { size: 14, color: C.soft, alpha: k * kn }));
    if (n.buttons) {
      const bw = (nw - 28 - (n.buttons.length - 1) * 10) / n.buttons.length, by = ny + nh - 50;
      n.buttons.forEach((b, i) => {
        const bx = nx + 14 + i * (bw + 10), kp = b.pressed ?? 0;
        const col = { go: C.good, no: C.bad }[b.kind] ?? C.soft;
        ctx.save(); ctx.globalAlpha *= k * kn;
        ctx.fillStyle = kp > 0 ? col : "transparent"; ctx.strokeStyle = col; ctx.lineWidth = 2;
        rr(bx, by, bw, 36, 8); if (kp > 0) ctx.fill(); ctx.stroke(); ctx.restore();
        text(b.label, bx + bw / 2, by + 24, { size: 16, weight: 700, font: COND, spacing: "1.5px", color: kp > 0 ? C.deep : col, align: "center", alpha: k * kn });
        if (kp > 0 && kp < 1) ring(bx + bw / 2, by + 18, 14 + kp * 40, col, 2, (1 - kp) * k);
      });
    }
  },

  /* The guarded switch: a striped cover that lifts (kg), then a lever that goes to GO (kp). */
  guard(x, y, kg, kp, k = 1) {
    if (k <= 0) return;
    const w = 150, h = 190;
    panel(x, y, w, h, { r: 4, fill: C.deep, stroke: C.line, lw: 2, alpha: k });
    text("GO", x + w / 2, y + 36, { size: 24, weight: 700, font: COND, spacing: "4px", align: "center", color: kp > 0.5 ? C.good : C.faint, alpha: k });
    text("HOLD", x + w / 2, y + h - 16, { size: 18, weight: 600, font: COND, spacing: "3px", align: "center", color: kp > 0.5 ? C.faint : C.accent, alpha: k });
    const cx = x + w / 2, top = y + 62, bot = y + h - 52, ly = lerp(bot, top, inOut(clamp(kp)));
    ctx.save(); ctx.globalAlpha *= k;
    ctx.strokeStyle = C.line; ctx.lineWidth = 10; ctx.lineCap = "round";
    ctx.beginPath(); ctx.moveTo(cx, top); ctx.lineTo(cx, bot); ctx.stroke();
    ctx.fillStyle = kp > 0.5 ? C.good : C.accent; rr(cx - 26, ly - 13, 52, 26, 5); ctx.fill();
    // the cover: hazard stripes, hinged at the top, folding away as kg goes to 1
    const ch = (h - 60) * (1 - outCubic(clamp(kg)));
    if (ch > 2) {
      ctx.beginPath(); ctx.rect(x + 12, y + 50, w - 24, ch); ctx.clip();
      ctx.fillStyle = "rgba(255,176,0,0.92)"; ctx.fillRect(x + 12, y + 50, w - 24, ch);
      ctx.strokeStyle = C.deep; ctx.lineWidth = 12;
      for (let i = -200; i < 200; i += 34) { ctx.beginPath(); ctx.moveTo(x + i, y + 50 + 200); ctx.lineTo(x + i + 200, y + 50); ctx.stroke(); }
    }
    ctx.restore();
  },

  /* A padlock: this thing can only read. */
  lock(x, y, s, color = C.faint, k = 1) {
    if (k <= 0) return;
    ctx.save(); ctx.globalAlpha *= k; ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = s * 0.14;
    ctx.beginPath(); ctx.arc(x, y - s * 0.12, s * 0.28, Math.PI, 0); ctx.stroke();
    rr(x - s * 0.4, y - s * 0.12, s * 0.8, s * 0.6, s * 0.08); ctx.fill(); ctx.restore();
  },

  /* Screenshots of the real console (synthetic data). K.load(names) before VIDEO.create. */
  images: {},
  ready: Promise.resolve(),
  load(names, base = "../shots/") {
    K.ready = Promise.all(names.map(n => new Promise(res => {
      const im = new Image();
      im.onload = () => { K.images[n] = im; res(); };
      im.onerror = () => res();
      im.src = base + n + ".webp";
    })));
    return K.ready;
  },
  /* Draw a screenshot at width w. o.crop = [sx, sy, sw, sh] as fractions of the image. */
  shot(name, x, y, w, o = {}) {
    const im = K.images[name], k = o.k ?? 1;
    if (!im || k <= 0) return 0;
    const [fx, fy, fw, fh] = o.crop ?? [0, 0, 1, 1];
    const sw = im.width * fw, sh = im.height * fh, h = w * sh / sw;
    ctx.save(); ctx.globalAlpha *= k;
    if (o.shadow !== false) { ctx.shadowColor = "rgba(0,0,0,0.5)"; ctx.shadowBlur = 30; ctx.shadowOffsetY = 12; }
    rr(x, y, w, h, 6); ctx.fillStyle = "#fff"; ctx.fill(); ctx.shadowColor = "transparent";
    rr(x, y, w, h, 6); ctx.clip();
    ctx.drawImage(im, im.width * fx, im.height * fy, sw, sh, x, y, w, h);
    ctx.restore();
    ctx.save(); ctx.globalAlpha *= k; rr(x, y, w, h, 6); ctx.strokeStyle = C.line; ctx.lineWidth = 1.5; ctx.stroke(); ctx.restore();
    return h;
  },

  /* Call after VIDEO.create: frames wait for the screenshots, and the poster is redrawn with them. */
  finish() {
    const fonts = window.videoReady;
    window.videoReady = Promise.all([fonts, K.ready]).then(() => true);
  },
};
