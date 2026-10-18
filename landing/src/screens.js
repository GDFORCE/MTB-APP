// The app screens shown on the 3D phone, drawn with Canvas 2D in the app's own
// look (frontend/src/theme/tokens.ts): blue-to-navy header, light canvas, white
// cards. One canvas is redrawn whenever the phone changes screen.

export const SCREEN_W = 360;
export const SCREEN_H = 780;

const T = {
  canvas: "#F4F6F9",
  surface: "#EDF1F6",
  ink: "#0F1B2D",
  muted: "#556275",
  border: "#DDE3EA",
  blue: "#1B5FC1",
  navy: "#0D2A4F",
  tint: "#E6EEFA",
  tintFg: "#123F80",
  teal: "#0B8074",
  tealTint: "#E0F3EF",
  amber: "#9A5B00",
  amberTint: "#FFF2D6",
  red: "#C23030",
  redTint: "#FAE6E6",
  green: "#1E7A46",
  greenTint: "#E2F3EA",
  apricot: "#F0782F",
  apricotTint: "#FFF1E6",
};
const FONT = '"Figtree", system-ui, -apple-system, "Segoe UI", sans-serif';
const MONO = '"Spline Sans Mono", ui-monospace, Consolas, monospace';
const PILL = {
  blue: [T.tint, T.tintFg],
  green: [T.greenTint, T.green],
  teal: [T.tealTint, T.teal],
  red: [T.redTint, T.red],
  amber: [T.amberTint, T.amber],
  white: ["rgba(255,255,255,0.2)", "#fff"],
};

function kit(c) {
  const k = {
    rr(x, y, w, h, r, fill, stroke) {
      c.beginPath();
      if (c.roundRect) c.roundRect(x, y, w, h, r);
      else c.rect(x, y, w, h);
      if (fill) {
        c.fillStyle = fill;
        c.fill();
      }
      if (stroke) {
        c.strokeStyle = stroke;
        c.lineWidth = 1;
        c.stroke();
      }
    },
    text(s, x, y, { size = 13, weight = 400, color = T.ink, align = "left", mono = false, spacing = 0 } = {}) {
      c.font = `${weight} ${size}px ${mono ? MONO : FONT}`;
      c.fillStyle = color;
      c.textAlign = align;
      c.textBaseline = "alphabetic";
      if ("letterSpacing" in c) c.letterSpacing = `${spacing}px`;
      c.fillText(s, x, y);
      return c.measureText(s).width;
    },
    label(s, x, y, color = T.muted) {
      return k.text(s.toUpperCase(), x, y, { size: 9.5, weight: 500, color, mono: true, spacing: 0.7 });
    },
    line(x1, x2, y) {
      c.fillStyle = T.surface;
      c.fillRect(x1, y, x2 - x1, 1);
    },
    dot(x, y, r, fill) {
      c.beginPath();
      c.arc(x, y, r, 0, Math.PI * 2);
      c.fillStyle = fill;
      c.fill();
    },
    check(x, y, s, color) {
      c.beginPath();
      c.moveTo(x - s * 0.5, y);
      c.lineTo(x - s * 0.12, y + s * 0.38);
      c.lineTo(x + s * 0.55, y - s * 0.4);
      c.strokeStyle = color;
      c.lineWidth = s * 0.26;
      c.lineCap = "round";
      c.lineJoin = "round";
      c.stroke();
    },
    header(eyebrow, title, pill) {
      const g = c.createLinearGradient(0, 0, 260, 150);
      g.addColorStop(0, "#2A6FD0");
      g.addColorStop(0.45, T.blue);
      g.addColorStop(1, T.navy);
      c.fillStyle = g;
      c.fillRect(0, 0, SCREEN_W, 136);
      k.label(eyebrow, 20, 84, "rgba(255,255,255,0.78)");
      k.text(title, 20, 114, { size: 24, weight: 700, color: "#fff" });
      if (pill) k.pill(pill[0], 340, 96, pill[1], true);
    },
    card(y, h, fill = "#fff", stroke = T.border) {
      k.rr(16, y, 328, h, 14, fill, stroke);
    },
    // Pill anchored by its right edge when `right` is set, otherwise its left.
    pill(s, x, y, kind = "blue", right = false) {
      c.font = `600 10.5px ${FONT}`;
      if ("letterSpacing" in c) c.letterSpacing = "0px";
      const w = c.measureText(s).width + 16;
      const x0 = right ? x - w : x;
      const [bg, fg] = PILL[kind];
      k.rr(x0, y, w, 20, 10, bg);
      k.text(s, x0 + 8, y + 14, { size: 10.5, weight: 600, color: fg });
      return w;
    },
    kv(name, value, y, { mono = false, pill } = {}) {
      k.text(name, 32, y, { size: 12.5, color: T.muted });
      if (pill) k.pill(value, 328, y - 14, pill, true);
      else k.text(value, 328, y, { size: 12.5, weight: 600, align: "right", mono });
    },
    kvs(y, rows, step = 33) {
      rows.forEach(([name, value, opts], i) => {
        if (i) k.line(32, 328, y + i * step - 21);
        k.kv(name, value, y + i * step, opts);
      });
    },
    row(title, sub, y, pill) {
      k.text(title, 32, y, { size: 13, weight: 600 });
      k.text(sub, 32, y + 17, { size: 11.5, color: T.muted });
      if (pill) k.pill(pill[0], 328, y - 6, pill[1], true);
    },
    rows(y, rows, step = 48) {
      rows.forEach(([title, sub, pill], i) => {
        if (i) k.line(32, 328, y + i * step - 19);
        k.row(title, sub, y + i * step, pill);
      });
    },
    tiles(y, items) {
      items.forEach(([value, name], i) => {
        const x = 16 + (i % 2) * 168;
        const ty = y + Math.floor(i / 2) * 70;
        k.rr(x, ty, 160, 62, 12, "#fff", T.border);
        k.text(value, x + 14, ty + 30, { size: 20, weight: 500, color: T.navy, mono: true });
        k.text(name, x + 14, ty + 49, { size: 11.5, color: T.muted });
      });
    },
    bar(x, y, w, pct) {
      k.rr(x, y, w, 7, 4, T.surface);
      const g = c.createLinearGradient(x, 0, x + w, 0);
      g.addColorStop(0, "#2A6FD0");
      g.addColorStop(1, T.teal);
      k.rr(x, y, w * pct, 7, 4, g);
    },
    button(s, y) {
      k.rr(16, y, 328, 46, 12, T.blue);
      k.text(s, 180, y + 28, { size: 14, weight: 600, color: "#fff", align: "center" });
    },
    tabbar(names, active) {
      c.fillStyle = "#fff";
      c.fillRect(0, 712, SCREEN_W, 68);
      c.fillStyle = T.border;
      c.fillRect(0, 712, SCREEN_W, 1);
      names.forEach((name, i) => {
        const x = 36 + i * 72;
        const on = i === active;
        k.rr(x - 11, 724, 22, 16, 5, on ? T.blue : null, on ? null : "#9AA6B6");
        k.text(name, x, 756, { size: 9.5, weight: on ? 700 : 500, color: on ? T.blue : T.muted, align: "center" });
      });
    },
  };
  return k;
}

const PATIENT_TABS = ["Home", "My Visits", "Calendar", "Messages", "Me"];
const PRO_TABS = ["Home", "Trials", "Calendar", "Messages", "Me"];

const SCHEDULE_ROWS = [
  ["V1", "Screening", "Day −14", "—"],
  ["V2", "Baseline", "Day 0", "—"],
  ["V3", "Week 2", "14 d", "±1 d"],
  ["V4", "Week 4", "14 d", "−2 / +3 d"],
  ["V5", "Week 8", "28 d", "±3 d"],
  ["V6", "Week 12", "28 d", "±3 d"],
  ["V7", "Week 16", "28 d", "±5 d"],
];

function scheduleTable(k, y, flagged) {
  k.card(y, 40 + SCHEDULE_ROWS.length * 37);
  k.label("Visit", 32, y + 24);
  k.label("Gap", 190, y + 24);
  k.label("Window", 328 - 62, y + 24);
  SCHEDULE_ROWS.forEach(([no, name, gap, win], i) => {
    const ry = y + 40 + i * 37;
    const flag = flagged.includes(no);
    if (flag) k.rr(22, ry - 1, 316, 35, 8, T.amberTint);
    else k.line(32, 328, ry - 1);
    k.text(no, 32, ry + 22, { size: 12, weight: 500, color: flag ? T.amber : T.blue, mono: true });
    k.text(name, 62, ry + 22, { size: 12.5, weight: 600 });
    k.text(gap, 190, ry + 22, { size: 12, color: T.muted, mono: true });
    k.text(win, 328 - 62, ry + 22, { size: 12, weight: 500, mono: true, color: flag ? T.amber : T.ink });
  });
}

const SCREENS = {
  home(k) {
    k.header("My Trial Board", "Good morning");
    k.card(152, 128, T.apricotTint, "#F8D7BE");
    k.label("Next visit · Visit 4", 32, 178, T.apricot);
    k.pill("Scheduled", 328, 164, "blue", true);
    k.text("Wed 11 Nov 2026", 32, 210, { size: 23, weight: 700, color: T.navy });
    k.text("Week 4 follow-up · window 9 – 14 Nov", 32, 232, { size: 12.5, color: T.muted });
    k.dot(37, 257, 4, T.apricot);
    k.text("City General Hospital · Dr. V. Rao", 48, 261, { size: 12, weight: 500 });

    k.card(292, 76);
    k.label("Trial progress", 32, 316);
    k.bar(32, 328, 296, 0.25);
    k.text("3 of 12 visits completed", 32, 356, { size: 11.5, color: T.muted });

    k.card(380, 98);
    k.label("This week", 32, 404);
    ["M", "T", "W", "T", "F", "S", "S"].forEach((d, i) => {
      const x = 50 + i * 43.5;
      k.text(d, x, 426, { size: 10, color: T.muted, align: "center", mono: true });
      if (i === 2) k.dot(x, 449, 15, T.apricot);
      else if (i < 6) k.rr(x - 15, 434, 30, 30, 15, T.tint);
      k.text(String(9 + i), x, 454, { size: 12.5, weight: 600, align: "center", color: i === 2 ? "#fff" : i < 6 ? T.tintFg : T.muted });
    });

    k.card(490, 124);
    k.label("Notifications", 32, 514);
    k.dot(37, 538, 4, T.blue);
    k.text("Reminder: Visit 4 is in 1 week", 48, 542, { size: 12.5, weight: 600 });
    k.text("Today · 8:00 AM", 48, 558, { size: 11, color: T.muted });
    k.line(32, 328, 570);
    k.dot(37, 588, 4, T.teal);
    k.text("Visit 3 completed", 48, 592, { size: 12.5, weight: 600 });
    k.text("28 Oct · next visit 11 Nov", 48, 608, { size: 11, color: T.muted });
    k.tabbar(PATIENT_TABS, 0);
  },

  addTrial(k) {
    k.header("New trial", "Add trial");
    k.card(152, 76);
    k.rr(32, 168, 34, 44, 6, T.tint);
    k.rr(39, 180, 20, 3, 1.5, T.blue);
    k.rr(39, 188, 20, 3, 1.5, T.blue);
    k.rr(39, 196, 13, 3, 1.5, T.blue);
    k.text("Protocol_MTB-ONC-204.pdf", 78, 186, { size: 13, weight: 600 });
    k.text("Uploaded · details extracted", 78, 204, { size: 11.5, color: T.teal, weight: 500 });
    k.dot(314, 190, 11, T.tealTint);
    k.check(314, 190, 10, T.teal);

    k.card(240, 312);
    k.label("Trial details · review before saving", 32, 264);
    k.kvs(292, [
      ["Protocol ID", "MTB-ONC-204", { mono: true }],
      ["CTRI number", "CTRI/2026/08/012345", { mono: true }],
      ["Phase", "Phase III"],
      ["Indication", "Breast cancer"],
      ["Drug", "MTB-204"],
      ["Sample size", "240", { mono: true }],
      ["Total visits", "12", { mono: true }],
      ["Trial status", "Active", { pill: "green" }],
    ]);
    k.button("Save trial", 620 - 48);
    k.tabbar(PRO_TABS, 1);
  },

  draft(k) {
    k.header("MTB-ONC-204", "Visit schedule", ["AI draft", "white"]);
    k.rr(16, 150, 328, 52, 12, T.amberTint, "#F1D9A6");
    k.text("AI-generated schedule. It may contain", 32, 172, { size: 11.5, weight: 600, color: T.amber });
    k.text("mistakes — check carefully before saving.", 32, 188, { size: 11.5, weight: 600, color: T.amber });
    scheduleTable(k, 214, ["V4", "V6"]);
    k.text("2 items need your review", 32, 538, { size: 12, weight: 600, color: T.amber });
    k.button("Review flagged items", 556);
    k.tabbar(PRO_TABS, 1);
  },

  approved(k) {
    k.header("MTB-ONC-204", "Visit schedule", ["v3 · Reviewed", "white"]);
    k.rr(16, 150, 328, 52, 12, T.greenTint, "#BFE3CF");
    k.dot(40, 176, 11, T.green);
    k.check(40, 176, 10, "#fff");
    k.text("Reviewed and saved", 60, 172, { size: 12.5, weight: 700, color: T.green });
    k.text("Dr. V. Rao · Principal Investigator", 60, 188, { size: 11.5, color: T.green });
    scheduleTable(k, 214, []);
    k.card(524, 176);
    k.label("Version history", 32, 548);
    k.rows(574, [
      ["v3 · Window for V6 changed", "Current version", ["Current", "teal"]],
      ["v2 · ECG added at Week 12", "Saved by Dr. V. Rao"],
      ["v1 · AI draft reviewed", "Saved by Dr. V. Rao"],
    ], 44);
    k.tabbar(PRO_TABS, 1);
  },

  patient(k) {
    k.header("MTB-ONC-204", "Add patient");
    k.card(152, 150);
    k.kvs(180, [
      ["Subject ID", "S-0142", { mono: true }],
      ["Subject initials", "R.K."],
      ["Anchor", "Randomization"],
      ["Anchor date", "14 Oct 2026", { mono: true }],
    ]);
    k.card(314, 262);
    k.label("Generated schedule", 32, 338);
    k.rows(366, [
      ["V2 · Baseline", "Wed 14 Oct 2026", ["Anchor", "blue"]],
      ["V3 · Week 2", "Wed 28 Oct · window 27 – 29 Oct"],
      ["V4 · Week 4", "Wed 11 Nov · window 9 – 14 Nov"],
      ["V5 · Week 8", "Wed 9 Dec · window 6 – 12 Dec"],
    ]);
    k.text("+ 8 more visits", 32, 562, { size: 11.5, weight: 600, color: T.blue });
    k.button("Save schedule", 592);
    k.tabbar(PRO_TABS, 1);
  },

  visit(k) {
    k.header("S-0142 · R.K.", "Visit 4 · Week 4");
    k.card(152, 118);
    k.kvs(180, [
      ["Scheduled date", "Wed 11 Nov 2026", { mono: true }],
      ["Actual visit date", "Wed 11 Nov 2026", { mono: true }],
      ["Status", "Visit completed", { pill: "green" }],
    ]);
    k.card(282, 86, T.tint, "#C9DBF5");
    k.label("Next visit", 32, 306, T.tintFg);
    k.text("Visit 5 · Wed 9 Dec 2026", 32, 332, { size: 16, weight: 700, color: T.navy });
    k.text("Week 8 · window 6 – 12 Dec", 32, 351, { size: 12, color: T.tintFg });
    k.card(380, 196);
    k.label("What happened next", 32, 404);
    [
      [T.teal, "Recruitment counters updated", "Sponsor sees totals only"],
      [T.blue, "Patient notified", "Visit completed · next visit 9 Dec"],
      [T.apricot, "Reminders scheduled", "1 week, 1 day and day of visit · 8:00 AM"],
    ].forEach(([color, title, sub], i) => {
      const y = 432 + i * 50;
      if (i) k.line(32, 328, y - 19);
      k.dot(38, y - 4, 5, color);
      k.text(title, 54, y, { size: 13, weight: 600 });
      k.text(sub, 54, y + 17, { size: 11.5, color: T.muted });
    });
    k.tabbar(PRO_TABS, 0);
  },

  sponsor(k) {
    k.header("Sponsor", "Dashboard");
    k.tiles(152, [["6", "Trials"], ["14", "Sites"], ["212", "Patients"], ["14", "PIs"]]);
    k.card(298, 208);
    k.label("MTB-ONC-204 · Phase III", 32, 322);
    k.kvs(350, [
      ["Total screened", "212", { mono: true }],
      ["Screen failures", "31", { mono: true }],
      ["Randomized", "164", { mono: true }],
      ["Completed", "48", { mono: true }],
    ]);
    k.bar(32, 466, 296, 0.68);
    k.text("164 of 240 enrolled · all sites", 32, 492, { size: 11.5, color: T.muted });
    k.card(518, 104);
    k.label("Site-wise · randomized", 32, 542);
    k.kvs(570, [
      ["City General Hospital", "22", { mono: true }],
      ["Lakeside Medical College", "18", { mono: true }],
    ]);
    k.tabbar(PRO_TABS, 0);
  },

  site(k) {
    k.header("Site admin", "Dashboard");
    k.tiles(152, [["4", "Trials"], ["7", "PIs"], ["86", "Patients"], ["3", "Sponsors"]]);
    k.card(298, 132);
    k.label("Overdue visits", 32, 322);
    k.rows(350, [
      ["S-0117 · A.M.", "V6 · Week 8 · Cardiology", ["2 days", "red"]],
      ["S-0093 · J.P.", "V3 · Week 2 · Oncology", ["1 day", "red"]],
    ]);
    k.card(442, 180);
    k.label("Today's visits", 32, 466);
    k.rows(494, [
      ["R.K. · S-0142", "V4 Follow-up · Dr. V. Rao", ["Scheduled", "blue"]],
      ["T.S. · S-0155", "V2 Baseline · Dr. V. Rao", ["Completed", "green"]],
      ["N.D. · S-0161", "V1 Screening · Dr. A. Mehta", ["Scheduled", "blue"]],
    ]);
    k.tabbar(PRO_TABS, 0);
  },

  pi(k) {
    k.header("Research team", "Today's visits");
    k.card(152, 280);
    k.label("Wed 11 Nov 2026", 32, 176);
    k.rows(206, [
      ["R.K. · S-0142", "V4 Follow-up · MTB-ONC-204", ["Scheduled", "blue"]],
      ["T.S. · S-0155", "V2 Baseline · MTB-ONC-204", ["Completed", "green"]],
      ["A.M. · S-0117", "V6 Week 8 · MTB-CAR-110", ["Overdue", "red"]],
      ["N.D. · S-0161", "V1 Screening · MTB-CAR-110", ["Scheduled", "blue"]],
    ], 56);
    k.card(444, 86, T.tint, "#C9DBF5");
    k.label("Upcoming · next 30 days", 32, 468, T.tintFg);
    k.text("18 visits across 2 trials", 32, 494, { size: 16, weight: 700, color: T.navy });
    k.text("3 windows open this week", 32, 513, { size: 12, color: T.tintFg });
    k.button("Update visit status", 556);
    k.tabbar(PRO_TABS, 0);
  },

  admin(k) {
    k.header("Platform admin", "Dashboard");
    k.tiles(152, [["1,284", "Users"], ["37", "Trials"], ["4", "Pending approvals"], ["9", "Open tickets"]]);
    k.card(298, 132);
    k.label("User management", 32, 322);
    k.rows(350, [
      ["P••••• S•••••", "+91 ••••• ••214 · Patient", ["Active", "green"]],
      ["Dr. V. Rao", "PI · City General Hospital", ["Active", "green"]],
    ]);
    k.card(442, 104);
    k.label("Notification delivery · today", 32, 466);
    k.kvs(494, [
      ["Delivered", "1,932", { mono: true }],
      ["Failed · retry available", "2", { mono: true }],
    ]);
    k.tabbar(["Home", "Users", "Orgs", "Audit", "Me"], 0);
  },
};

function chrome(k) {
  // Status bar, camera cut-out and home indicator, drawn over every screen.
  k.text("9:41", 30, 36, { size: 14, weight: 600, color: "#fff" });
  [0, 1, 2, 3].forEach((i) => k.rr(286 + i * 5, 34 - i * 3, 3.2, 3 + i * 3, 1, "#fff"));
  k.rr(312, 25, 24, 12, 3.5, null, "rgba(255,255,255,0.75)");
  k.rr(314, 27, 16, 8, 2, "#fff");
  k.rr(132, 12, 96, 28, 14, "#05070A");
  k.rr(124, 767, 112, 4.5, 2.25, "rgba(15,27,45,0.85)");
}

export const SCREEN_IDS = Object.keys(SCREENS);

export function drawScreen(canvas, id, scale = 2) {
  canvas.width = Math.round(SCREEN_W * scale);
  canvas.height = Math.round(SCREEN_H * scale);
  const c = canvas.getContext("2d");
  c.setTransform(scale, 0, 0, scale, 0, 0);
  c.fillStyle = T.canvas;
  c.fillRect(0, 0, SCREEN_W, SCREEN_H);
  const k = kit(c);
  SCREENS[id](k);
  chrome(k);
  return canvas;
}

/** The screen fonts must be loaded before the first draw or text falls back. */
export async function screenFontsReady() {
  if (!document.fonts?.load) return;
  const faces = ['400 13px "Figtree"', '500 13px "Figtree"', '600 13px "Figtree"', '700 13px "Figtree"', '500 13px "Spline Sans Mono"'];
  await Promise.race([Promise.all(faces.map((f) => document.fonts.load(f))), new Promise((r) => setTimeout(r, 2500))]);
}
