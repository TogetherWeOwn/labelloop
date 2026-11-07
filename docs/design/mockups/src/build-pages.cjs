// Screens 02-10. Each page is one HTML file; fixture names and numbers are invented.
const fs = require("fs");
const path = require("path");
const { I, pg, badge, header, overlay, page, footer, OBJECTS, W, H } = require("./parts.cjs");

// Scrollable table regions need unique names: number them per page.
const out = (f, s) => {
  let n = 0;
  fs.writeFileSync(path.join(__dirname, f), s.replaceAll('aria-label="Table"', () => `aria-label="Table ${++n}, scrolls sideways"`));
};
const shell = (cur, title, body, opts = {}) =>
  page(title, `${header(cur, opts)}\n<main id="main" class="page">${body}</main>\n${footer}`, opts);
const phead = (h1, gloss, lede, acts = "", meta = "") => `<div class="phead"><div>${meta ? `<p class="label" style="margin-bottom:8px">${meta}</p>` : ""}<h1>${h1} <span class="g">${gloss}</span></h1>${lede ? `<p class="lede">${lede}</p>` : ""}</div>${acts ? `<div class="acts">${acts}</div>` : ""}</div>`;
const meter = (pct, cls = "") => `<span class="meter big ${cls}" aria-hidden="true"><span style="width:${Math.min(pct, 100)}%"></span></span>`;
const delta = (v, unit = "", good = "up") => {
  if (v === 0) return '<span class="t3">–</span>';
  const up = v > 0;
  const cls = (up ? "up" : "down") === good || good === "any" ? "up" : "down";
  return `<span class="delta ${cls}">${up ? I.up : I.down}${up ? "+" : "−"}${Math.abs(v)}${unit}<span class="sr"> ${up ? "up" : "down"}</span></span>`;
};
const tile = (v, c, extra = "") => `<div class="tile"><span class="v">${v}</span><span class="c">${c}</span>${extra}</div>`;

// 02 New project ------------------------------------------------------------
function newProject() {
  const steps = [
    ["Scope", "done", "Plant milk 1 L on shelves"],
    ["Classes", "current", "4 classes · draft"],
    ["Evidence", "", "References and confusion pairs"],
    ["Labelling guide", "", "Boxes, edges, tiny objects"],
    ["Sources and groups", "", "Upload, hashes, split groups"],
    ["Check and publish", "", "Freeze project release 1"],
  ];
  const rows = [
    ["Oat milk 1 L", "oat, oat drink", "Plant milk", "12", "Barista oat 1 L", "Yes"],
    ["Soy milk 1 L", "soya, soy drink", "Plant milk", "9", "Soy dessert 500 ml", "Yes"],
    ["Almond milk 1 L", "almond drink", "Plant milk", "4", "—", "Yes"],
    ["Price tag", "shelf label", "—", "6", "Promo stickers", "No"],
  ];
  const body = `${phead("New project.", "Set it up once, then let it learn.", "Nothing is labelled until you approve the classes. You can come back to any step; the draft saves as you go.", "", "Draft · saved 01:20")}
<div class="grid2">
  <section class="card" aria-labelledby="cls-h">
    <header><h2 id="cls-h">Classes <span class="t3 small">step 2 of 6</span></h2><div class="chips"><button class="chip">Import from a labelled dataset</button><button class="chip">Draft with the assistant</button></div></header>
    <p class="small t2">Each class gets a permanent id. Renaming keeps it. Merging or splitting classes later makes a new ontology version.</p>
    <div class="tablewrap" tabindex="0" role="region" aria-label="Table"><table><caption class="sr">Classes in this draft</caption>
      <thead><tr><th scope="col">Class</th><th scope="col">Aliases</th><th scope="col">Parent</th><th scope="col" class="r">Examples</th><th scope="col">Not this</th><th scope="col">Mirror-safe</th><th scope="col"><span class="sr">Actions</span></th></tr></thead>
      <tbody>${rows
        .map(
          (r) =>
            `<tr><th scope="row">${r[0]}</th><td class="t2">${r[1]}</td><td class="t2">${r[2]}</td><td class="r">${r[3] < 5 ? `<span class="tag warn">${r[3]} · add more</span>` : r[3]}</td><td class="t2">${r[4]}</td><td>${r[5]}</td><td class="r"><button class="pill ghost" aria-label="Edit ${r[0]}">${I.plus.replace("M12 5v14M5 12h14", "M4 20h4L19 9l-4-4L4 16z")}</button></td></tr>`,
        )
        .join("")}</tbody></table></div>
    <div class="field"><label for="newc">Add a class</label><div style="display:flex;gap:8px"><input id="newc" class="input" placeholder="For example: Rice milk 1 L"><button class="pill ghost md">${I.plus}Add</button></div><p class="help">Mirror-safe means a left-right flip doesn't change the class. Text on packs usually means no.</p></div>
    <div style="display:flex;gap:8px;justify-content:space-between;flex-wrap:wrap"><a class="pill ghost md" href="#">${I.back}Back to scope</a><button class="pill md">Approve classes and continue</button></div>
  </section>
  <aside class="stack" aria-label="Setup steps">
    <nav class="card" aria-label="Setup steps"><h2>Setup</h2><ol class="steps" role="list">${steps
      .map(
        ([t, st, sub], i) =>
          `<li class="${st === "done" ? "done" : ""}"${st === "current" ? ' aria-current="step"' : ""}><span class="n">0${i + 1}</span><span>${t}<span class="sub t3 small" style="display:block;font-weight:400">${sub}</span></span>${st === "done" ? `<span class="tag ok">Done</span>` : st === "current" ? '<span class="tag">Now</span>' : ""}</li>`,
      )
      .join("")}</ol></nav>
    <div class="notice">${I.info}<p>Automation stays off for a class until it has enough reviewed examples and passes its pilot. Almond milk needs at least 1 more.</p></div>
  </aside>
</div>`;
  out("new-project.html", shell("", "New project", body, { role: "Admin" }));
}

// 03 Dataset releases -----------------------------------------------------
function releases() {
  const cls = [
    ["Oat milk 1 L", 2140, 312, 288, 120],
    ["Soy milk 1 L", 1860, 266, 251, 98],
    ["Almond milk 1 L", 512, 71, 69, -14],
    ["Price tag", 3920, 560, 541, 402],
  ];
  const max = 3920 + 560 + 541;
  const body = `${phead("Release r0008.", "Draft, compared with r0007.", "A release freezes exactly which images and label revisions go into training and evaluation. Nothing in a frozen release can change.", '<a class="pill ghost md" href="#">Download manifest</a><button class="pill md">Freeze r0008</button>', "Draft · built 01:40 from 1,326 images")}
<div class="tiles" role="list">
  <div role="listitem">${tile('1,326 <span class="small">images</span>', `${delta(86)} since r0007`)}</div>
  <div role="listitem">${tile('11,082 <span class="small">objects</span>', `${delta(731)} since r0007`)}</div>
  <div role="listitem">${tile('23 <span class="small">excluded</span>', `${delta(-4, "", "down")} since r0007`)}</div>
  <div role="listitem">${tile("100%", "of included images checked complete")}</div>
</div>
<div class="grid2" style="margin-top:24px">
  <div class="stack">
    <section class="card" aria-labelledby="prov-h">
      <header><h2 id="prov-h">Where the labels came from</h2><span class="label">Training split only</span></header>
      <div class="keyrow"><span class="key">${pg("reviewed")}Reviewed by a person · 6,904</span><span class="key">${pg("auto")}Auto-accepted, policy P-2 · 3,412</span></div>
      <span class="dist" role="img" aria-label="Training labels: 6,904 reviewed by a person, 3,412 auto-accepted"><span style="width:67%;background:var(--two-color-label-reviewed)"></span><span style="width:33%;background:var(--two-color-label-auto)"></span></span>
      <p class="small t2">Validation and test use reviewed labels only. Proposed and uncertain labels never enter a release.</p>
    </section>
    <section class="card" aria-labelledby="dist-h">
      <header><h2 id="dist-h">Class distribution</h2><div class="keyrow"><span class="key"><i style="background:var(--two-color-text-secondary)"></i>Train</span><span class="key"><i style="background:var(--two-color-text-tertiary)"></i>Val</span><span class="key"><i style="background:var(--two-color-line-control)"></i>Test</span></div></header>
      <div class="tablewrap" tabindex="0" role="region" aria-label="Table"><table><caption class="sr">Objects per class and split in r0008, and the change since r0007</caption>
        <thead><tr><th scope="col">Class</th><th scope="col" style="width:38%"><span class="sr">Distribution</span></th><th scope="col" class="r">Train</th><th scope="col" class="r">Val</th><th scope="col" class="r">Test</th><th scope="col" class="r">Change</th></tr></thead>
        <tbody>${cls
          .map(
            ([n, tr, va, te, d]) =>
              `<tr><th scope="row">${n}</th><td><span class="dist" aria-hidden="true" style="width:${(((tr + va + te) / max) * 100).toFixed(0)}%"><span class="tr" style="width:${((tr / (tr + va + te)) * 100).toFixed(0)}%"></span><span class="va" style="width:${((va / (tr + va + te)) * 100).toFixed(0)}%"></span><span class="te" style="width:${((te / (tr + va + te)) * 100).toFixed(0)}%"></span></span></td><td class="r">${tr.toLocaleString("en-GB")}</td><td class="r">${va}</td><td class="r">${te}</td><td class="r">${delta(d)}</td></tr>`,
          )
          .join("")}</tbody></table></div>
      <div class="notice warn">${I.warn}<p>Almond milk lost 14 objects: an adjudication moved them to Soy milk. Its test split has 69 objects, under the 100 the per-class gate needs, so it will be marked unsupported in evaluation.</p></div>
    </section>
  </div>
  <aside class="stack" aria-label="Changes and exclusions">
    <section class="card" aria-labelledby="diff-h"><h2 id="diff-h">Changes since r0007</h2>
      <dl class="facts"><dt>Images added</dt><dd>+92</dd><dt>Images removed</dt><dd>−6</dd><dt>Labels changed</dt><dd>148</dd><dt>Class changes</dt><dd>31</dd><dt>Ontology</dt><dd>v1, unchanged</dd><dt>Split groups</dt><dd>v3, unchanged</dd></dl>
      <a class="small" href="#">See every changed image</a></section>
    <section class="card" aria-labelledby="exc-h"><h2 id="exc-h">Excluded · 23</h2>
      <ul class="hist" role="list">
        <li>${pg("uncertain")}<span>11 images have an uncertain object waiting for adjudication</span><a class="small" href="#">Open</a></li>
        <li>${pg("rejected")}<span>7 images have a target under 4 px at 320</span><a class="small" href="#">Open</a></li>
        <li>${pg("rejected")}<span>5 images moved to quarantine: a new near-duplicate links them to the test split</span><a class="small" href="#">Open</a></li>
      </ul></section>
  </aside>
</div>`;
  out("releases.html", shell("releases", "Release r0008", body, { role: "Admin" }));
}

// 04 Size and crop recipes -----------------------------------------------
function recipes() {
  const frame = (S, label, flags) => {
    const contentH = (S * 1080) / 1920; // letterboxed height in output px
    const padPct = (((S - contentH) / 2 / S) * 100).toFixed(2);
    const contentPct = ((contentH / S) * 100).toFixed(2);
    return `<figure class="frame" style="width:${S * 0.75}px"><div class="sq" role="img" aria-label="${label}: the full 1920 by 1080 frame letterboxed to ${S} by ${S}, ${Math.round(contentH)} px of picture and ${Math.round((S - contentH) / 2)} px of padding above and below.">
      <span class="pad" style="top:0;height:${padPct}%"></span>
      <span class="content" style="top:${padPct}%;height:${contentPct}%"><img src="scene.svg" alt="">${overlay(OBJECTS.map((o) => ({ ...o, tier: o.tiny ? "flag" : "auto", kind: null })), { width: S * 0.75, tabs: "none", uid: `r${S}` })}</span>
      <span class="pad" style="bottom:0;height:${padPct}%"></span>
    </div><figcaption class="small t2"><b class="t1" style="color:var(--two-color-text-primary)">${label}</b> · ${S} × ${S} · picture ${S} × ${Math.round(contentH)} · padding ${Math.round((S - contentH) / 2)} px · scale ${(S / 1920).toFixed(3)}</figcaption>${flags}</figure>`;
  };
  const body = `${phead("Size and crop recipes.", "See what the model will see.", "Each recipe builds its own dataset from the original pixels. Check the preview before you compile: small targets shrink, and some won't survive.", '<button class="pill md">Compile r0008 with 2 recipes</button>')}
<nav class="seg" aria-label="Recipes" style="margin-bottom:20px"><a href="#" aria-current="page">full-letterbox-640-v1</a><a href="#" aria-current="page">full-letterbox-320-v1</a><a href="#">${I.plus}Add recipe</a></nav>
<div class="card">
  <header><h2>Preview · IMG_0412.png</h2><div class="chips"><button class="chip">${I.back}Previous</button><button class="chip">Next flagged image${I.next}</button></div></header>
  <div class="frames">
    ${frame(640, "640 recipe", '<p class="small"><span class="tag warn">2 flagged</span> 2 price tags are 14 × 7 px. They train, but watch them in evaluation.</p>')}
    ${frame(320, "320 recipe", '<p class="small"><span class="tag danger">2 below 4 px</span> Both price tags become 7 × 3 px. This image is excluded from the 320 dataset unless you recrop.</p>')}
  </div>
  <p class="label">Both shown at 0.75× so sizes compare · thick red box = flagged target · hatched = padding (grey 114 in the output)</p>
</div>
<div class="grid2 even" style="margin-top:24px">
  <section class="card" aria-labelledby="flag-h"><h2 id="flag-h">Across r0008 · 1,326 images</h2>
    <div class="tablewrap" tabindex="0" role="region" aria-label="Table"><table><caption class="sr">Flagged targets per recipe</caption><thead><tr><th scope="col">Flag</th><th scope="col" class="r">640</th><th scope="col" class="r">320</th></tr></thead><tbody>
      <tr><th scope="row">Target 6 px or smaller (flagged)</th><td class="r">412</td><td class="r">3,920</td></tr>
      <tr><th scope="row">Target under 4 px (excluded or recropped)</th><td class="r">0</td><td class="r">488</td></tr>
      <tr><th scope="row">Clipped at an image edge</th><td class="r">61</td><td class="r">61</td></tr>
      <tr><th scope="row">Images excluded</th><td class="r">0</td><td class="r">212</td></tr>
    </tbody></table></div>
    <div class="notice danger">${I.warn}<p>The 320 recipe would drop 212 images, mostly for price tags. Consider a tile recipe for price tags, or accept that 320 is qualified for cartons only.</p></div>
  </section>
  <section class="card" aria-labelledby="set-h"><h2 id="set-h">full-letterbox-320-v1</h2>
    <dl class="facts"><dt>Field of view</dt><dd>Full frame</dd><dt>Resize</dt><dd>Letterbox, centred</dd><dt>Down / up</dt><dd>area / linear</dd><dt>Padding</dt><dd class="mono">114, 114, 114</dd><dt>Smallest target</dt><dd>4 px</dd><dt>Flag at or below</dt><dd>6 px</dd><dt>Version</dt><dd>v1 · locked by r0007</dd></dl>
    <p class="small t2">A recipe used by a frozen release can't change. Edit makes v2.</p><button class="pill ghost" style="align-self:flex-start">Make v2</button>
  </section>
</div>`;
  out("recipes.html", shell("recipes", "Recipes", body, { role: "Admin" }));
}

// 05 Training runs -------------------------------------------------------
function runs() {
  const rows = [
    ["run-0031", "full-letterbox-640-v1", "r0008", "Local", "Running", "41 of 100 epochs", "—"],
    ["run-0030", "full-letterbox-320-v1", "r0008", "Platform", "Queued", "waiting for GPU", "$1.38 est."],
    ["run-0029", "full-letterbox-640-v1", "r0007", "Platform", "Completed", "100 epochs · 1 h 52 m", "$1.29"],
    ["run-0028", "full-letterbox-320-v1", "r0007", "Platform", "Failed", "args mismatch", "$0.17"],
  ];
  const st = { Running: "tag", Queued: "tag", Completed: "tag ok", Failed: "tag danger" };
  const body = `${phead("Training runs.", "One per recipe.", "", '<button class="pill ghost md" aria-expanded="true" aria-controls="start">Start a run</button>')}
<section id="start" class="confirm" aria-labelledby="start-h" style="margin-bottom:24px">
  <h2 id="start-h" style="font-size:var(--two-font-size-ui)">Start run on Ultralytics Platform</h2>
  <div class="grid2 even" style="gap:20px">
    <dl class="facts"><dt>Release</dt><dd>r0008</dd><dt>Recipe</dt><dd>full-letterbox-320-v1</dd><dt>Base model</dt><dd class="mono">yolo26n · sha256 9c1e…</dd><dt>GPU</dt><dd>RTX 4090 · 3 free</dd><dt>Epochs</dt><dd>100</dd><dt>Images</dt><dd>1,114</dd></dl>
    <div class="stack" style="gap:12px">
      <div class="tile"><span class="label">Estimate <span class="tag warn">Estimate</span></span><span class="v">$1.38</span><span class="c">About 2 h at $0.69/h. Billed for time used, even if cancelled; at least 15 minutes.</span></div>
      <p class="small t2">This month after this run: $4.48 of $40. Per-run limit $5.</p>
      <p class="small t2">The run won't start if its estimate is over $5, or would take this month's spend past $40. Time-based billing can come in above the estimate; the actual shows as unconfirmed until the bill is matched to this run.</p>
    </div>
  </div>
  <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="pill md">Start · estimate $1.38</button><button class="pill ghost md">Cancel</button></div>
</section>
<div class="tablewrap" tabindex="0" role="region" aria-label="Table"><table><caption class="sr">Training runs, newest first</caption>
<thead><tr><th scope="col">Run</th><th scope="col">Recipe</th><th scope="col">Release</th><th scope="col">Backend</th><th scope="col">Status</th><th scope="col">Progress</th><th scope="col" class="r">Cost</th></tr></thead>
<tbody>${rows
    .map(
      (r, i) =>
        `<tr${i === 3 ? ' class="hl"' : ""}><th scope="row" class="mono"><a href="#">${r[0]}</a></th><td class="mono small">${r[1]}</td><td class="mono">${r[2]}</td><td>${r[3]}</td><td><span class="${st[r[4]]}">${r[4]}</span></td><td class="t2">${i === 0 ? `<span style="display:flex;gap:8px;align-items:center"><span class="meter" style="width:80px" role="img" aria-label="41 of 100 epochs"><span style="width:41%"></span></span>41 of 100</span>` : r[5]}</td><td class="r">${r[6]}</td></tr>`,
    )
    .join("")}</tbody></table></div>
<div class="grid2" style="margin-top:24px">
  <section class="card" aria-labelledby="r28-h"><header><h2 id="r28-h">run-0028 failed: settings didn't match</h2><span class="tag danger">Failed</span></header>
    <p class="small t2">After start, labelloop read back the settings the platform used. 2 differ from what we asked for, so the run can't be trusted and was stopped at 14 minutes.</p>
    <div class="tablewrap" tabindex="0" role="region" aria-label="Table"><table><caption class="sr">Requested and resolved training settings that differ</caption><thead><tr><th scope="col">Setting</th><th scope="col">Asked for</th><th scope="col">Platform used</th></tr></thead><tbody>
      <tr><th scope="row" class="mono">fliplr</th><td class="mono">0.0</td><td class="mono down">0.5</td></tr>
      <tr><th scope="row" class="mono">scale</th><td class="mono">0.0</td><td class="mono down">missing (default 0.5)</td></tr>
    </tbody></table></div>
    <div style="display:flex;gap:8px"><button class="pill ghost">Copy settings to a new run</button><a class="pill ghost" href="#">Open in Platform ${I.ext}</a></div>
  </section>
  <aside class="stack" aria-label="Running now">
    <section class="card" aria-labelledby="r31-h"><h2 id="r31-h">run-0031 · running</h2>
      <dl class="facts"><dt>Epoch</dt><dd>41 of 100</dd><dt>Best val mAP50-95</dt><dd>0.612 · epoch 38</dd><dt>Checkpoints</dt><dd>41 saved, hashed</dd><dt>Targets kept</dt><dd>12,410 of 12,418</dd><dt>Updated</dt><dd class="mono">01:52 · 20 s ago</dd></dl>
      <p class="small t2">8 targets dropped by the loader: all under 3 px. <a href="#">See them</a></p>
      <button class="pill danger" style="align-self:flex-start">Cancel run</button></section>
  </aside>
</div>`;
  out("runs.html", shell("runs", "Training runs", body, { role: "Admin" }));
}

// 06 Evaluation --------------------------------------------------------------
function evaluation() {
  const cls = [
    ["Oat milk 1 L", 0.71, 0.74, 312],
    ["Soy milk 1 L", 0.69, 0.68, 266],
    ["Almond milk 1 L", null, null, 69],
    ["Price tag", 0.38, 0.47, 560],
  ];
  const slices = [
    ["Targets under 16 px (source)", 0.31, 0.42, 488],
    ["Clipped at edge", 0.52, 0.55, 61],
    ["Occluded", 0.48, 0.47, 140],
    ["Empty shelves (negatives)", "2 false", "1 false", 40],
  ];
  const body = `${phead("Evaluation.", "Candidate against current, 640.", "Scored on the original frames: predictions are mapped back through the recipe and checked against full-image labels. Ultralytics val numbers are kept as a diagnostic only.", '<a class="pill ghost md" href="#">Download report</a><a class="pill md" href="#">Go to promotion</a>', "eval-0044 · test set t-2 (locked) · 1,114 frames · 01:58")}
<nav class="seg" aria-label="Recipe" style="margin-bottom:20px"><a href="#" aria-current="page">640</a><a href="#">320</a></nav>
<div class="tiles" role="list">
  <div role="listitem">${tile("0.634", `mAP50-95 · current 0.602 ${delta(0.032)}`)}</div>
  <div role="listitem">${tile("0.921", `Precision at conf 0.25 · ${delta(0.004)}`)}</div>
  <div role="listitem">${tile("0.874", `Recall · ${delta(0.019)}`)}</div>
  <div role="listitem">${tile("3 of 4", "gates passed")}</div>
</div>
<div class="grid2" style="margin-top:24px">
  <div class="stack">
    <section class="card" aria-labelledby="pc-h"><header><h2 id="pc-h">mAP50-95 per class</h2><div class="keyrow"><span class="key"><i style="background:var(--two-color-text-tertiary)"></i>Current m-0007</span><span class="key"><i style="background:var(--two-color-text-primary)"></i>Candidate run-0029</span></div></header>
      <div class="bars" aria-hidden="true">${cls
        .map(([n, a, b, sup]) =>
          a === null
            ? `<div class="bar-row"><span>${n}</span><span class="small t3">Unsupported: ${sup} test objects, gate needs 100</span><span></span></div>`
            : `<div class="bar-row"><span>${n}</span><span class="bar-pair"><span class="inc" style="width:${a * 100}%"></span><span class="cand" style="width:${b * 100}%"></span></span><span class="mono small">${b.toFixed(2)} ${b >= a ? "▲" : "▼"}</span></div>`,
        )
        .join("")}</div>
      <details><summary class="small">Show as a table</summary><div class="tablewrap" tabindex="0" role="region" aria-label="Table" style="margin-top:8px"><table><caption class="sr">mAP50-95 per class</caption><thead><tr><th scope="col">Class</th><th scope="col" class="r">Support</th><th scope="col" class="r">Current</th><th scope="col" class="r">Candidate</th></tr></thead><tbody>${cls
        .map(([n, a, b, s]) => `<tr><th scope="row">${n}</th><td class="r">${s}</td><td class="r">${a ?? "unsupported"}</td><td class="r">${b ?? "unsupported"}</td></tr>`)
        .join("")}</tbody></table></div></details>
      <p class="sr">Oat milk: current 0.71, candidate 0.74. Soy milk: current 0.69, candidate 0.68. Almond milk: unsupported, 69 test objects. Price tag: current 0.38, candidate 0.47.</p>
    </section>
    <section class="card" aria-labelledby="sl-h"><h2 id="sl-h">Hard conditions</h2>
      <div class="tablewrap" tabindex="0" role="region" aria-label="Table"><table><caption class="sr">Recall per slice, current against candidate</caption><thead><tr><th scope="col">Slice</th><th scope="col" class="r">Objects</th><th scope="col" class="r">Current</th><th scope="col" class="r">Candidate</th><th scope="col" class="r">Change</th></tr></thead><tbody>${slices
        .map(([n, a, b, s]) => `<tr><th scope="row">${n}</th><td class="r">${s}</td><td class="r">${a}</td><td class="r">${b}</td><td class="r">${typeof a === "number" ? delta(Math.round((b - a) * 100) / 100) : delta(-1, " false", "down")}</td></tr>`)
        .join("")}</tbody></table></div>
      <p class="small t2">Coverage lost to cropping or exclusion: 0 frames at 640.</p>
    </section>
  </div>
  <aside class="card" aria-labelledby="g-h"><h2 id="g-h">Gates for 640</h2>
    <ul class="checks" role="list">
      <li><span class="ok-i">${I.check}</span><span>No critical class drops more than 0.02</span></li>
      <li><span class="ok-i">${I.check}</span><span>Recall on small targets doesn't drop</span></li>
      <li><span class="ok-i">${I.check}</span><span>Export matches native within 0.005</span></li>
      <li><span style="color:var(--two-color-state-danger)">${I.x}</span><span>Every class has 100 test objects. Almond milk has 69, so the candidate can't qualify for it.</span></li>
    </ul>
    <div class="notice warn">${I.warn}<p>Qualifies for 3 of 4 classes. You can promote it with Almond milk marked unsupported, or keep the current model.</p></div>
    <p class="label">Locked test set t-2 was opened once for this candidate</p>
  </aside>
</div>`;
  out("evaluation.html", shell("evaluation", "Evaluation", body, { role: "Admin" }));
}

// 07 Model registry -------------------------------------------------------
function models() {
  const recipeCard = (r, cur, cand, ok) => `<section class="card" aria-labelledby="m${r}-h">
  <header><h2 id="m${r}-h">${r} · full-letterbox-${r}-v1</h2><span class="label">Independent of the other recipe</span></header>
  <div class="grid2 even" style="gap:16px">
    <div class="tile"><span class="label">In use</span><span class="v mono">${cur[0]}</span><span class="c">${cur[1]}</span></div>
    <div class="tile" style="box-shadow:inset 0 0 0 1px var(--two-color-line-control)"><span class="label">Candidate ${ok ? '<span class="tag ok">Qualifies</span>' : '<span class="tag danger">Not qualified</span>'}</span><span class="v mono">${cand[0]}</span><span class="c">${cand[1]}</span></div>
  </div>
  <details${r === "640" ? " open" : ""}><summary class="small">Deployment contract for ${cand[0]}</summary>
    <dl class="facts" style="margin-top:8px"><dt>Weights</dt><dd class="mono">sha256 4f7a…c210</dd><dt>Input</dt><dd>${r} × ${r}, letterbox, static</dd><dt>Classes</dt><dd class="mono">class-map 3b9e…</dd><dt>NMS</dt><dd>external · IoU 0.7 · max 300</dd><dt>Tiling</dt><dd>off</dd><dt>Export</dt><dd>ONNX · opset 19</dd><dt>Evaluations</dt><dd><a href="#">eval-0044</a></dd></dl></details>
  <div style="display:flex;gap:8px;flex-wrap:wrap">${ok ? `<button class="pill md">Promote ${cand[0]} for ${r}</button>` : `<button class="pill ghost md" aria-disabled="true" aria-describedby="why${r}">Promote</button><p id="why${r}" class="help" style="align-self:center">Fails the small-target recall gate. <a href="#">See evaluation</a></p>`}</div>
</section>`;
  const body = `${phead("Models.", "One champion per recipe.", "Promoting swaps the weights, preprocessing, classes and thresholds together. The previous model stays ready for roll back.")}
<div class="stack">
  ${recipeCard("640", ["m-0007", "since 30 Oct · proposes labels for new images"], ["m-0009", "mAP50-95 0.634 · +0.032"], true)}
  ${recipeCard("320", ["m-0006", "since 24 Oct"], ["m-0008", "mAP50-95 0.488 · recall on small targets −0.06"], false)}
  <section class="card" aria-labelledby="hist-h"><h2 id="hist-h">History</h2>
    <div class="tablewrap" tabindex="0" role="region" aria-label="Table"><table><caption class="sr">Promotions and roll backs</caption><thead><tr><th scope="col">When (UTC)</th><th scope="col">Recipe</th><th scope="col">Change</th><th scope="col">By</th><th scope="col"><span class="sr">Actions</span></th></tr></thead><tbody>
      <tr><td class="mono">30 Oct 14:02</td><td>640</td><td>Promoted m-0007, replaced m-0005</td><td>Ade</td><td class="r"><button class="pill ghost">Roll back 640 to m-0005</button></td></tr>
      <tr><td class="mono">24 Oct 09:40</td><td>320</td><td>Promoted m-0006, replaced m-0004</td><td>Ade</td><td class="r"><button class="pill ghost">Roll back 320 to m-0004</button></td></tr>
      <tr><td class="mono">21 Oct 18:15</td><td>640</td><td>Rolled back to m-0005: drift on store 9</td><td>Mora</td><td></td></tr>
    </tbody></table></div></section>
</div>`;
  out("models.html", shell("models", "Models", body, { role: "Admin" }));
}

// 08 Connections ------------------------------------------------------------
function connections() {
  const op = (t, ok, why) => `<li><span class="${ok ? "ok-i" : "todo-i"}">${ok ? I.check : I.x}</span><span>${t}${why ? ` <span class="t3">· ${why}</span>` : ""}</span></li>`;
  const body = `${phead("Connections.", "Keys stay on the server.", "labelloop never shows a key again after you save it. You see a fingerprint so you can tell keys apart.")}
<div class="grid2">
  <div class="stack">
    <section class="card" aria-labelledby="ul-h"><header><h2 id="ul-h">Ultralytics Platform</h2><span class="tag ok">Connected</span></header>
      <dl class="facts"><dt>Workspace</dt><dd class="mono">shelf-team</dd><dt>Owner</dt><dd>Ade</dd><dt>Checked</dt><dd class="mono">01:30 UTC · 25 min ago</dd></dl>
      <div class="secret" aria-label="Key fingerprint">${I.lock}<span>ul_••••9f3c</span><span class="t3">sha256 1a2b3c4d</span><span class="t3" style="margin-left:auto">added 2 Nov by Ade</span></div>
      <div class="notice warn">${I.warn}<p>This key has full owner rights on the workspace and never expires. labelloop only uses it for the operations below, and every paid one goes through Costs.</p></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="pill ghost">Replace key</button><button class="pill ghost">Check now</button><button class="pill danger">Disconnect</button></div>
    </section>
    <section class="card" aria-labelledby="llm-h"><header><h2 id="llm-h">LLM review providers</h2><button class="pill ghost">${I.plus}Add provider</button></header>
      <div class="tablewrap" tabindex="0" role="region" aria-label="Table"><table><caption class="sr">LLM providers</caption><thead><tr><th scope="col">Provider</th><th scope="col">Key</th><th scope="col">Fixture check</th><th scope="col">Sees images</th></tr></thead><tbody>
        <tr><th scope="row">OpenAI-compatible · gpt-vision-2</th><td class="mono">••••a71d · 77e0c2</td><td><span class="tag ok">Passed 01:10</span></td><td>Yes</td></tr>
        <tr><th scope="row">Anthropic · claude-opus-5-5</th><td class="mono">••••02be · 1f9a44</td><td><span class="tag danger">Failed</span> <span class="small t2">schema drift</span></td><td>Yes</td></tr>
        <tr><th scope="row">Local · llava-next</th><td class="t3">No key</td><td><span class="tag ok">Passed 00:55</span></td><td>Yes, on this server</td></tr>
      </tbody></table></div>
      <p class="small t2">A provider that fails its fixture check isn't used until it passes again.</p>
    </section>
  </div>
  <aside class="stack" aria-label="What labelloop may do">
    <section class="card" aria-labelledby="ops-h"><h2 id="ops-h">What labelloop may do here</h2>
      <ul class="checks" role="list">${op("Upload and check releases", true)}${op("Start training runs", true, "with your approval")}${op("Download weights", true)}${op("Batch auto-annotation", false, "off by policy")}${op("Delete datasets", false, "never")}</ul></section>
    <section class="card" aria-labelledby="px-h"><h2 id="px-h">Who may see this project's images</h2>
      <p class="small t2">Source images are shelf photos from your stores. Only providers ticked here receive pixels.</p>
      <div class="toggle"><span>OpenAI-compatible</span><button class="switch" role="switch" aria-checked="true" aria-label="OpenAI-compatible may see images"></button></div>
      <div class="toggle"><span>Anthropic</span><button class="switch" role="switch" aria-checked="true" aria-label="Anthropic may see images"></button></div>
      <div class="toggle"><span>Local</span><button class="switch" role="switch" aria-checked="true" aria-label="Local may see images"></button></div>
    </section>
  </aside>
</div>`;
  out("connections.html", shell("", "Connections", body, { role: "Admin" }));
}

// 09 Costs -------------------------------------------------------------------
function costs() {
  const led = [
    ["01:52", "Training · run-0030 · 320", "Ade", "$1.38", "—", "Queued"],
    ["01:12", "LLM review · 49 images", "Policy (autonomous)", "$0.41", "$0.37", "Done"],
    ["30 Oct 12:10", "Training · run-0029 · 640", "Ade", "$1.32", "$1.29", "Done"],
    ["29 Oct 18:40", "Training · run-0028 · 320", "Ade", "$1.32", "$0.17", "Failed · stopped at 14 m"],
    ["29 Oct 09:02", "Batch auto-annotation · 900 images", "Refused", "$0.90", "—", "Over per-operation limit"],
  ];
  const body = `${phead("Costs.", "Nothing paid runs without an estimate.", "", "", "November · USD · prices table v4")}
<div class="notice danger" style="margin-bottom:20px">${I.warn}<div><p>Paid steps are paused: Ultralytics Platform said the account is out of credits (402). Top up the account, then resume. labelloop won't retry on its own.</p><div class="acts"><button class="pill ghost">Resume paid steps</button></div></div></div>
<div class="tiles" role="list">
  <div role="listitem">${tile("$3.10 <span class='small t2'>of $40</span>", "spent this month", meter(8))}</div>
  <div role="listitem">${tile("$1.38", '<span class="tag warn">Estimate</span> reserved for queued work')}</div>
  <div role="listitem">${tile("$0.41 / $1.00", "this iteration", meter(41))}</div>
  <div role="listitem">${tile("−$0.03", "actual against estimate, this month")}</div>
</div>
<div class="grid2" style="margin-top:24px">
  <section class="card" aria-labelledby="led-h"><header><h2 id="led-h">Ledger</h2><a class="small" href="#">Download CSV</a></header>
    <div class="tablewrap" tabindex="0" role="region" aria-label="Table"><table><caption class="sr">Every paid operation, newest first</caption><thead><tr><th scope="col">When (UTC)</th><th scope="col">Operation</th><th scope="col">Approved by</th><th scope="col" class="r">Estimate</th><th scope="col" class="r">Actual</th><th scope="col">Result</th></tr></thead><tbody>${led
      .map((r) => `<tr><td class="mono">${r[0]}</td><td>${r[1]}</td><td>${r[2] === "Refused" ? '<span class="tag danger">Refused</span>' : r[2]}</td><td class="r">${r[3]}</td><td class="r">${r[4]}</td><td class="t2">${r[5]}</td></tr>`)
      .join("")}</tbody></table></div></section>
  <aside class="stack" aria-label="Budget and policy">
    <section class="card" aria-labelledby="pol-h"><h2 id="pol-h">Spending policy</h2>
      <div class="toggle"><span>Spend without asking</span><button class="switch" role="switch" aria-checked="false" aria-describedby="auto-why" aria-label="Spend without asking"></button><span id="auto-why" class="why">Off: every paid step waits for a person to approve its estimate. On: steps run inside the limits below, and each one still writes its estimate first.</span></div>
      <div class="field"><label for="b1">Monthly limit</label><input id="b1" class="input" value="$40.00"></div>
      <div class="field"><label for="b2">Per iteration</label><input id="b2" class="input" value="$1.00"></div>
      <div class="field"><label for="b3">Per operation</label><input id="b3" class="input" value="$5.00"><p class="help">An operation over any limit is refused, never queued.</p></div>
      <button class="pill md" style="align-self:flex-start">Save limits</button>
    </section>
  </aside>
</div>`;
  out("costs.html", shell("costs", "Costs", body, { role: "Admin", spend: [3.1, 40], paused: true }));
}

// 10 Audit log ----------------------------------------------------------------
function audit() {
  const ev = [
    ["01:58:12", "Ade", "person", "Promotion", "Promoted m-0009 for 640, replacing m-0007", "eval-0044"],
    ["01:52:40", "Ade", "person", "Budget", "Approved $1.38 for run-0030", "ledger 118"],
    ["01:41:03", "Mora", "person", "Review", "Accepted IMG_0412.png: 5 proposals decided, 1 box added", "rev 7"],
    ["01:12:55", "worker-llm", "service", "Review", "Proposed a class change on object 4 in IMG_0412.png", "rev 5"],
    ["01:30:00", "worker-platform", "service", "Connection", "Checked Ultralytics key ••••9f3c: workspace shelf-team", "—"],
    ["00:58:21", "worker-llm", "service", "Review", "Tried to mark object 2 reviewed: refused, models can't set human fields", "rev 4"],
  ];
  const body = `${phead("Audit log.", "Who did what, and as whom.", "Every review, promotion, connection and budget event, with the signed-in person or the service that did it. Entries can't be edited.")}
<div class="toolbar" style="position:static;padding:0 0 16px;background:none;border:0;backdrop-filter:none;flex-wrap:wrap">
  <div class="chips" role="group" aria-label="Kind">${["Review", "Promotion", "Connection", "Budget"].map((k) => `<button class="chip" aria-pressed="true">${k}</button>`).join("")}</div>
  <div class="chips" role="group" aria-label="Who"><button class="chip" aria-pressed="true">People</button><button class="chip" aria-pressed="true">Services</button></div>
  <span class="grow"></span><label class="sr" for="q">Find in the log</label><div class="input sm" style="width:260px">${I.search}<input id="q" class="ph" style="background:none;border:0;color:inherit;font:inherit;width:100%" placeholder="Image, run, model or person"></div>
</div>
<div class="tablewrap" tabindex="0" role="region" aria-label="Table"><table><caption class="sr">Audit events, 7 November, newest first</caption><thead><tr><th scope="col">Time (UTC)</th><th scope="col">Who</th><th scope="col">Kind</th><th scope="col">What happened</th><th scope="col">Record</th></tr></thead><tbody>${ev
  .map(
    (e) =>
      `<tr><td class="mono">${e[0]}</td><td>${e[2] === "service" ? `<span class="mono">${e[1]}</span> <span class="tag">Service</span>` : e[1]}</td><td>${e[3]}</td><td>${e[4].includes("refused") ? `<span class="down">${e[4]}</span>` : e[4]}</td><td class="mono"><a href="#">${e[5]}</a></td></tr>`,
  )
  .join("")}</tbody></table></div>
<p class="label" style="margin-top:12px">Today · Sat 7 Nov · 6 of 214 events · <a href="#">Export JSON lines</a></p>`;
  out("audit.html", shell("audit", "Audit log", body, { role: "Admin" }));
}

newProject();
releases();
recipes();
runs();
evaluation();
models();
connections();
costs();
audit();
console.log("pages written");
