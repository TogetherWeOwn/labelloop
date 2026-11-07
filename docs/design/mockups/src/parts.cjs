// Shared fragments for the labelloop mockups. Fixture names and numbers are invented.
const { W, H, CLASSES, OBJECTS } = require("./fixture.cjs");

const svg = (body, cls = "ico") =>
  `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true">${body}</svg>`;

// Our own 24 px stroke set (kit tokens.md §7), plus the workbench additions.
const I = {
  back: svg('<path d="m14 6-6 6 6 6"/>'),
  next: svg('<path d="m10 6 6 6-6 6"/>'),
  down: svg('<path d="m6 9 6 6 6-6"/>', "ico s"),
  up: svg('<path d="m6 15 6-6 6 6"/>', "ico s"),
  select: svg('<path d="M6 4l12 7-5.5 1.5L10 18z"/>'),
  box: svg('<rect x="4.5" y="6" width="15" height="12" rx="1"/><path d="M4.5 3v3M3 6h3"/>'),
  split: svg('<rect x="4" y="6" width="16" height="12" rx="1"/><path d="M12 4v16" stroke-dasharray="2 2"/>'),
  merge: svg('<rect x="3.5" y="7" width="8" height="10" rx="1"/><rect x="12.5" y="7" width="8" height="10" rx="1"/><path d="M9 12h6"/>'),
  zoom: svg('<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.4-4.4M8.5 11h5M11 8.5v5"/>'),
  eye: svg('<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.8"/>'),
  key: svg('<rect x="3" y="6.5" width="18" height="11" rx="2"/><path d="M7 10h.01M11 10h.01M15 10h.01M8 14h8"/>'),
  check: svg('<path d="m5 12.5 4.5 4.5L19 7.5"/>'),
  circle: svg('<circle cx="12" cy="12" r="7"/>'),
  info: svg('<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 8h.01"/>'),
  warn: svg('<path d="M12 4 21 19H3z"/><path d="M12 10v4M12 17h.01"/>'),
  x: svg('<path d="m7 7 10 10M17 7 7 17"/>'),
  search: svg('<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.4-4.4"/>'),
  menu: svg('<path d="M4 7h16M4 12h16M4 17h16"/>'),
  lock: svg('<rect x="5" y="10.5" width="14" height="10" rx="2.5"/><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5"/>', "ico s"),
  plus: svg('<path d="M12 5v14M5 12h14"/>'),
  undo: svg('<path d="M9 7 4.5 11.5 9 16"/><path d="M5 11.5h9a5 5 0 0 1 0 10h-2"/>'),
  ext: svg('<path d="M14 5h5v5M19 5l-8 8M10 6H6a1 1 0 0 0-1 1v11a1 1 0 0 0 1 1h11a1 1 0 0 0 1-1v-4"/>', "ico s"),
};

// J3 provenance glyphs: shapes differ so tiers survive greyscale and forced colours.
const PG = {
  proposed:
    '<circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" stroke-width="2" stroke-dasharray="2.6 2"/>',
  auto: '<path d="M8 2.2 13.8 8 8 13.8 2.2 8z" fill="none" stroke="currentColor" stroke-width="2"/>',
  reviewed:
    '<circle cx="8" cy="8" r="6.5" fill="currentColor"/><path d="m5 8.2 2 2 4-4.2" fill="none" stroke="var(--two-color-bg-canvas)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
  uncertain:
    '<circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-dasharray="1.2 1.6"/><path d="M6.3 6.3a1.8 1.8 0 1 1 2.4 1.7c-.5.2-.7.5-.7 1v.4M8 11.4h.01" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>',
  rejected:
    '<circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="m5.6 5.6 4.8 4.8m0-4.8-4.8 4.8" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
};
const pg = (tier) => `<svg class="pg ${tier}" viewBox="0 0 16 16" aria-hidden="true">${PG[tier]}</svg>`;

const TIER_WORD = {
  proposed: "Proposed",
  auto: "Auto-accepted",
  reviewed: "Reviewed",
  uncertain: "Uncertain",
  rejected: "Rejected",
};
// J2 provenance badge: glyph + word, always together.
const badge = (tier, word = TIER_WORD[tier]) => `<span class="tag ${tier}">${pg(tier)}${word}</span>`;

const brandMark = `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"><rect x="3.5" y="3.5" width="11" height="11" rx="1.5"/><path d="M9.5 20.5h9a2 2 0 0 0 2-2v-9" /><path d="m17.5 6.5 3 3 3-3" transform="translate(-3 0)"/></svg>`;

const SECTIONS = [
  ["review", "Review", "37"],
  ["releases", "Releases"],
  ["recipes", "Recipes"],
  ["runs", "Runs"],
  ["evaluation", "Evaluation"],
  ["models", "Models"],
  ["costs", "Costs"],
  ["audit", "Audit log"],
];

// J1 workbench header. `cur` names the current section.
const header = (cur, { spend = [3.1, 40], role = "Reviewer", project = "Shelf audit" } = {}) => {
  const pct = Math.round((spend[0] / spend[1]) * 100);
  return `<a class="skip pill" href="#main">Skip to content</a>
<header class="top"><div class="bar">
  <a class="brand" href="#">${brandMark}<span>labelloop</span></a>
  <button class="project" aria-haspopup="listbox" aria-label="Project: ${project}. Switch project">${project}${I.down}</button>
  <nav class="sections" aria-label="Project"><ul role="list">${SECTIONS.map(
    ([k, l, n]) =>
      `<li><a href="#"${k === cur ? ' aria-current="page"' : ""}>${l}${n ? `<span class="count">${n}<span class="sr"> images to review</span></span>` : ""}</a></li>`,
  ).join("")}</ul></nav>
  <div class="end">
    <a class="spend hide-m" href="#" aria-label="Spend this month: $${spend[0].toFixed(2)} of $${spend[1]} budget. Open costs">
      <span>$${spend[0].toFixed(2)} of $${spend[1]}</span><span class="meter" aria-hidden="true"><span style="width:${pct}%"></span></span>
    </a>
    <a class="who" href="#"><span class="av" aria-hidden="true">MO</span><span class="hide-m">Mora · ${role}</span><span class="sr">Mora, ${role}. Account and connections</span></a>
    <button class="pill ghost icon menu-btn" aria-expanded="false" aria-label="Menu">${I.menu}</button>
  </div>
</div></header>`;
};

// Scale used when a box overlay is drawn at a given rendered width.
const overlay = (objects, { width = 860, selected = null, drawing = null, tabs = "numbers", hide = [], uid = "h" } = {}) => {
  const s = width / W;
  const u = (px) => px / s; // screen px -> source units
  const rect = (b, cls, extra = "") =>
    `<rect class="${cls}" x="${b[0]}" y="${b[1]}" width="${b[2] - b[0]}" height="${b[3] - b[1]}" ${extra}/>`;
  const corners = (b) => {
    const L = u(9);
    const [x0, y0, x1, y1] = b;
    return `<path class="corner" d="M${x0} ${y0 + L}V${y0}H${x0 + L}M${x1 - L} ${y0}H${x1}V${y0 + L}M${x1} ${y1 - L}V${y1}H${x1 - L}M${x0 + L} ${y1}H${x0}V${y1 - L}"/>`;
  };
  const handles = (b) => {
    const [x0, y0, x1, y1] = b;
    const xm = (x0 + x1) / 2;
    const ym = (y0 + y1) / 2;
    const h = u(8);
    return [
      [x0, y0],
      [xm, y0],
      [x1, y0],
      [x1, ym],
      [x1, y1],
      [xm, y1],
      [x0, y1],
      [x0, ym],
    ]
      .map(([x, y]) => `<rect class="handle" x="${x - h / 2}" y="${y - h / 2}" width="${h}" height="${h}"/>`)
      .join("");
  };
  const g = [];
  const t = [];
  for (const o of objects) {
    if (hide.includes(o.tier)) continue;
    const sel = o.id === selected;
    const parts = [];
    if (o.kind === "geometry" || o.kind === "split")
      parts.push(`<g class="ghost">${rect(o.box, "halo")}${rect(o.box, "stroke")}</g>`);
    const shown = o.kind === "geometry" ? [o.toBox] : o.kind === "split" ? o.parts : [o.box];
    for (const b of shown) {
      parts.push(`<g class="${o.tier}">`);
      if (o.tier === "uncertain") parts.push(rect(b, "hatch", `fill="url(#hatch-${uid})"`));
      parts.push(rect(b, "halo") + rect(b, "stroke"));
      if (o.tier === "auto") parts.push(corners(b));
      parts.push("</g>");
    }
    if (sel) {
      const b = shown[0];
      const p = u(4);
      parts.push(
        `<g class="selected">${rect([b[0] - p, b[1] - p, b[2] + p, b[3] + p], "sel")}${handles(b)}</g>`,
      );
    }
    g.push(parts.join(""));
    // Tab: compact (glyph + number) unless selected or in names mode.
    const b = shown[0];
    const name = o.cls ? CLASSES[o.cls].short : "No class";
    let text = "";
    if (sel || tabs === "names") {
      if (o.kind === "class") text = `${name} → ${CLASSES[o.to].short}`;
      else if (o.kind === "geometry") text = `${name} · new box`;
      else if (o.kind === "split") text = "Split into 2";
      else if (o.kind === "new") text = `New · ${name}`;
      else if (o.tier === "uncertain") text = "Uncertain";
      else text = name;
    }
    if (tabs === "none") continue;
    // Placement: tiny boxes get a side tab; boxes at the right edge anchor the tab's end;
    // boxes at the top edge put the tab below. Tabs never leave the well.
    const side = o.tiny;
    const end = !side && b[0] / W > 0.9;
    const below = !side && b[1] < 60;
    const left = (((side ? b[2] : end ? b[2] : b[0]) / W) * 100).toFixed(3);
    const top = (((side ? (b[1] + b[3]) / 2 : below ? b[3] : b[1]) / H) * 100).toFixed(3);
    const place = side ? " side" : end ? " end" : below ? " below" : "";
    t.push(
      `<span class="btab ${o.tier}${sel ? " sel" : ""}${place}" style="left:${left}%;top:${top}%">${pg(o.tier)}<span class="id">${o.id}</span>${text ? `<span class="txt">${text}</span>` : ""}</span>`,
    );
  }
  if (drawing) {
    g.push(`<g class="drawing">${rect(drawing, "halo")}${rect(drawing, "stroke")}${handles(drawing)}</g>`);
  }
  return `<svg class="boxes" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">
<defs><pattern id="hatch-${uid}" patternUnits="userSpaceOnUse" width="${u(7)}" height="${u(7)}" patternTransform="rotate(45)"><rect width="${u(2)}" height="${u(7)}" style="fill:var(--two-color-label-uncertain)" opacity=".35"/></pattern></defs>
${g.join("\n")}</svg><div class="tabs" aria-hidden="true">${t.join("")}</div>`;
};

// Background CSS that shows source box `b` from scene.svg inside a frame of fw x fh px.
// Scale is 1:1 when the crop fits, otherwise the largest scale that fits (never above 4x).
const cropBg = (b, fw, fh, { pad = 0, maxScale = 4, fit = "contain" } = {}) => {
  const bx = [b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad];
  const cw = bx[2] - bx[0];
  const ch = bx[3] - bx[1];
  let s = fit === "contain" ? Math.min(fw / cw, fh / ch) : Math.max(fw / cw, fh / ch);
  s = Math.min(s, maxScale);
  const ox = (fw - cw * s) / 2 - bx[0] * s;
  const oy = (fh - ch * s) / 2 - bx[1] * s;
  return {
    s,
    css: `background-image:url(scene.svg);background-size:${(W * s).toFixed(1)}px ${(H * s).toFixed(1)}px;background-position:${ox.toFixed(1)}px ${oy.toFixed(1)}px`,
  };
};

const scaleWord = (s) => (Math.abs(s - 1) < 0.01 ? "1:1" : s > 1 ? `${s.toFixed(1)}× up` : `${s.toFixed(2)}×`);

const page = (title, body, { status = "" } = {}) => `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>${title} · labelloop</title><link rel="stylesheet" href="workbench.css"></head>
<body>${body}
<div role="status" aria-live="polite" class="sr">${status}</div>
</body></html>`;

const footer = `<footer class="foot"><span>labelloop is free software under the AGPL-3.0. <a href="#">Get the source code</a>.</span><span class="mono">v0.1.0 · ultralytics 8.4.204</span></footer>`;

module.exports = { I, pg, badge, TIER_WORD, header, overlay, cropBg, scaleWord, page, footer, OBJECTS, CLASSES, W, H };
