// Image review (screen 01): four states from one fixture. Writes review*.html and scene.svg.
const fs = require("fs");
const path = require("path");
const { sceneSvg, refSvg } = require("./fixture.cjs");
const { I, pg, badge, TIER_WORD, header, overlay, cropBg, scaleWord, page, footerSlim, OBJECTS, CLASSES } = require("./parts.cjs");

const out = (f, s) => fs.writeFileSync(path.join(__dirname, f), s);
out("scene.svg", sceneSvg());
for (const c of ["oat", "soy", "almond"]) for (const v of [0, 1, 2]) out(`ref-${c}-${v}.svg`, refSvg(c, v));

const CANVAS = 860; // rendered canvas width at 1280 (1280 - inspector 380 - 2 x 20 padding)
const ORDER = { proposed: 0, uncertain: 1, auto: 2, reviewed: 3 };
const name = (o) => (o.cls ? CLASSES[o.cls].name : "No class");
const counts = (objs) =>
  objs.reduce((a, o) => ((a[o.tier] = (a[o.tier] || 0) + 1), a), { proposed: 0, auto: 0, reviewed: 0, uncertain: 0 });

const toolbar = ({ tool = "select", audit = false, file = "IMG_0412.png", pos = "12 of 49" } = {}) => `<div class="toolbar" role="toolbar" aria-label="Review tools">
  <a class="crumb" href="#">${I.back}Queue<span class="mono t3">· 37 left</span></a>
  <span class="vsep" aria-hidden="true"></span>
  <div class="imgname"><b>${file}</b><span class="label">1920 × 1080 · store 14</span></div>
  ${audit ? '<span class="tag warn">Audit sample</span>' : ""}
  <span class="grow"></span>
  <div class="seg hide-m" role="group" aria-label="Tool">${[
    ["select", "Select", "V", I.select],
    ["box", "Box", "B", I.box],
    ["split", "Split", "S", I.split],
    ["merge", "Merge", "M", I.merge],
  ]
    .map(([k, l, key, ic]) => `<button aria-pressed="${k === tool}" aria-keyshortcuts="${key}" title="${l} (${key})">${ic}${l}</button>`)
    .join("")}</div>
  <button class="chip hide-m" aria-label="Box labels: numbers. Change">${I.eye}Numbers</button>
  <button class="chip hide-m" aria-label="Zoom: fit, 45 percent. Change">${I.zoom}<span class="num">45%</span></button>
  <span class="vsep hide-m" aria-hidden="true"></span>
  <span class="mono small t2 num" style="white-space:nowrap">${pos}</span>
  <a class="pill ghost icon" href="#" aria-label="Previous image" aria-keyshortcuts="Shift+N">${I.back}</a>
  <a class="pill ghost icon" href="#" aria-label="Next image" aria-keyshortcuts="N">${I.next}</a>
  <button class="pill ghost icon hide-m" aria-label="Keyboard shortcuts" aria-keyshortcuts="?">${I.key}</button>
</div>`;

const legend = (objs, { hidden = false } = {}) => {
  const c = counts(objs);
  if (hidden)
    return `<div class="legend" role="group" aria-label="Show boxes"><button class="chip" aria-pressed="true">${pg("reviewed")}Your boxes<span class="n">${objs.length}</span></button><span class="small t3" style="margin-left:auto">Proposals are hidden until you compare</span></div>`;
  return `<div class="legend" role="group" aria-label="Show boxes by provenance">${["proposed", "auto", "reviewed", "uncertain"]
    .map(
      (t) =>
        `<button class="chip" aria-pressed="${!hidden || t === "reviewed"}">${pg(t)}${TIER_WORD[t]}<span class="n">${c[t]}</span></button>`,
    )
    .join("")}<span class="small t3" style="margin-left:auto">${hidden ? "Proposals hidden until you compare" : `Showing all ${objs.length} objects`}</span></div>`;
};

const strip = (objs, cur) =>
  `<section aria-labelledby="crops-h"><h2 id="crops-h" class="sr">Objects, in the order to decide them</h2><ol class="strip" role="list">${[...objs]
    .sort((a, b) => ORDER[a.tier] - ORDER[b.tier] || a.id - b.id)
    .map((o) => {
      const { css } = cropBg(o.box, 60, 72, { pad: 4 });
      return `<li><button class="crop ${o.tier}"${o.id === cur ? ' aria-current="true"' : ""}><span class="sr">Object </span><span class="img" style="${css}"></span><span class="cap">${pg(o.tier)}${o.id}</span><span class="cap2">${o.cls ? CLASSES[o.cls].short : "No class"}</span><span class="sr">, ${TIER_WORD[o.tier]}</span></button></li>`;
    })
    .join("")}</ol></section>`;

const canvas = (objs, opts = {}) => `<div class="well">
  <figure class="scene">
    <img src="scene.svg" alt="${opts.alt || "Shelf photo, store 14 aisle 3: two shelves of 1 litre plant-milk cartons with price tags."}">
    ${overlay(objs, { width: CANVAS, ...opts })}
    ${opts.extra || ""}
  </figure>
  <span class="zoomnote">1 screen px = 2.2 source px</span>
</div>`;

const dock = ({ left, ready = false, note }) => `<div class="dock">
  <div class="label" style="display:flex;justify-content:space-between"><span>${note}</span><span class="num">${left}</span></div>
  <div class="row">
    ${
      ready
        ? '<button class="pill md">Accept image<kbd>Enter</kbd></button>'
        : `<button class="pill md" aria-disabled="true" aria-describedby="why-not">Accept image</button>`
    }
    <button class="pill md ghost">Skip</button>
  </div>
  ${ready ? "" : '<p id="why-not" class="help">Decide the 5 proposals first. Uncertain is a decision.</p>'}
  <button class="linkbtn" style="align-self:flex-start">Exclude this image…</button>
</div>`;

const compactNote = `<div class="notice only-m" role="note">${I.info}<p>Drawing, redrawing, split and merge need a screen 820 px or wider. Here you can decide proposals, change a class and mark uncertain.</p></div>`;

// R1: default, object 4 selected with a proposed class change.
function r1() {
  const o = OBJECTS.find((x) => x.id === 4);
  const nat = cropBg(o.box, 140, 270, { maxScale: 1 });
  const ctx = cropBg(o.box, 170, 270, { pad: 70 });
  const refs = (c, label) => `<div class="refgroup"><h3>${CLASSES[c].name}</h3><p class="label" style="margin-bottom:6px">${label}</p><div class="row">${[0, 1, 2]
    .map((v) => `<span class="ref" role="img" aria-label="${CLASSES[c].name} reference ${["front", "angled", "low light"][v]}" style="background-image:url(ref-${c}-${v}.svg)"></span>`)
    .join("")}</div></div>`;
  const inspector = `<aside class="inspector" aria-label="Selected object">
  <div class="scroll">
    <section class="sec" aria-labelledby="obj-h">
      <div class="objhead"><h2 id="obj-h"><span class="id">4</span>Soy milk 1 L</h2>${badge("proposed")}</div>
      <p class="small t2" style="margin-top:-6px">The detector found it. LLM review thinks the class is wrong. No person has looked yet.</p>
      <div class="native">
        <figure><span class="img" role="img" aria-label="Native crop of object 4, 140 by 270 pixels" style="display:block;width:140px;height:270px;${nat.css}"></span><figcaption class="label" style="margin-top:6px">Native · ${scaleWord(nat.s)}</figcaption></figure>
        <figure><span class="img" role="img" aria-label="Context crop around object 4" style="display:block;width:170px;height:270px;${ctx.css}"></span><figcaption class="label" style="margin-top:6px">Context · ${scaleWord(ctx.s)}</figcaption></figure>
      </div>
    </section>

    <section class="edit" aria-labelledby="edit-h">
      <div class="who-line">${pg("proposed")}<h3 id="edit-h" class="small" style="font-weight:600">LLM review proposes</h3><span class="mono small t3" style="margin-left:auto">01:12</span></div>
      <p class="change">Change class: <s>Soy milk 1 L</s> → Almond milk 1 L</p>
      <figure><blockquote><p>The band is terracotta and the print reads ALMOND. Soy references have a green band.</p></blockquote><figcaption class="label" style="margin-top:6px">Model's reason · not checked by a person</figcaption></figure>
      <p class="small t2">Evidence it cited: <a href="#">reference A-3</a>, <a href="#">reference A-7</a>, <a href="#">crop 4</a></p>
      <div class="acts">
        <button class="pill ghost" aria-keyshortcuts="A">Accept change<kbd>A</kbd></button>
        <button class="pill ghost" aria-keyshortcuts="R">Keep Soy milk<kbd>R</kbd></button>
        <button class="linkbtn" aria-keyshortcuts="U">Mark uncertain</button>
      </div>
    </section>

    <section class="sec" aria-labelledby="refs-h">
      <header><h2 id="refs-h">Reference views</h2><a class="small t2" href="#">All 14</a></header>
      <div class="refs">${refs("almond", "Proposed class")}${refs("soy", "Current class")}</div>
      <p class="label">Approved references only · evaluation images are never shown here</p>
    </section>

    <section class="sec" aria-labelledby="class-h">
      <h2 id="class-h">Class and box</h2>
      <div class="field"><label for="cls">Class</label><button id="cls" class="input sm" aria-haspopup="listbox" aria-keyshortcuts="C">Soy milk 1 L<span style="margin-left:auto" class="t3">${I.down}</span></button></div>
      <dl class="facts">
        <dt>Box</dt><dd class="mono">692, 250 → 832, 520</dd>
        <dt>Size</dt><dd>140 × 270 px</dd>
        <dt>At 640</dt><dd>47 × 90 px</dd>
        <dt>At 320</dt><dd>23 × 45 px</dd>
        <dt>Edge</dt><dd>Not clipped</dd>
      </dl>
      <div class="acts" style="display:flex;gap:8px;flex-wrap:wrap"><button class="pill ghost hide-m">${I.box}Redraw<kbd>B</kbd></button><button class="pill ghost hide-m">${I.split}Split<kbd>S</kbd></button><button class="pill danger">Delete box</button></div>
    </section>

    <section class="sec" aria-labelledby="hist-h">
      <h2 id="hist-h">History</h2>
      <ol class="hist" role="list">
        <li>${pg("proposed")}<span>Detector <span class="mono t3">yolo26n · r0007</span> proposed Soy milk 1 L, score 0.62</span><time datetime="2026-11-07T01:02Z">01:02</time></li>
        <li>${pg("proposed")}<span>LLM review <span class="mono t3">prompt v3 · run 0412-b</span> proposed Almond milk 1 L</span><time datetime="2026-11-07T01:12Z">01:12</time></li>
        <li><span></span><span class="t3">No person has reviewed this object.</span><span></span></li>
      </ol>
    </section>
  </div>
  ${dock({ left: "5 of 14 to decide", note: "This image" })}
</aside>`;
  const body = `${header("review")}
<main id="main">
${toolbar()}
<div class="review">
  <div class="stage">
    <h1 class="sr">Review IMG_0412.png</h1>
    ${compactNote}
    ${canvas(OBJECTS, { selected: 4 })}
    ${legend(OBJECTS)}
    ${strip(OBJECTS, 4)}
  </div>
  ${inspector}
</div></main>
${footerSlim}`;
  out("review.html", page("Review IMG_0412.png", body));
}

// R2: drawing a box the models missed (the faded oat carton), class picker open.
function r2() {
  const draw = [1052, 250, 1192, 520];
  const s = CANVAS / 1920;
  const popLeft = Math.round(draw[2] * s + 14);
  const popTop = Math.round(draw[1] * s - 30);
  const opt = (c, sub, sel = false) => {
    const { css } = cropBg([20, 25, 160, 295], 36, 36, {});
    return `<li class="opt" role="option" aria-selected="${sel}"><span class="thumb" style="background-image:url(ref-${c}-0.svg);background-size:contain;background-position:center"></span><span>${CLASSES[c].name}<span class="sub">${sub}</span></span>${sel ? I.check : ""}</li>`;
  };
  const pop = `<div class="pop" role="dialog" aria-labelledby="pick-h" style="left:${popLeft}px;top:${popTop}px">
    <h2 id="pick-h">Class for the new box</h2>
    <div class="input sm focus" role="combobox" aria-expanded="true" aria-controls="cls-list" aria-label="Find a class">${I.search}<span class="ph">Type a class or alias</span></div>
    <ul id="cls-list" class="opts" role="listbox" aria-label="Classes">
      ${opt("oat", "Reference match 0.81 · A-12", true)}
      ${opt("almond", "Reference match 0.32")}
      ${opt("soy", "Reference match 0.18")}
    </ul>
    <div style="display:flex;gap:8px;margin-top:10px;align-items:center"><button class="pill">Add Oat milk<kbd>Enter</kbd></button><button class="pill ghost">Cancel<kbd>Esc</kbd></button></div>
    <button class="linkbtn">None of these fits · mark uncertain</button>
  </div>`;
  const inspector = `<aside class="inspector" aria-label="New object">
  <div class="scroll">
    <section class="sec" aria-labelledby="new-h">
      <div class="objhead"><h2 id="new-h"><span class="id">15</span>New object</h2>${badge("reviewed", "Yours")}</div>
      <p class="small t2">Neither model found this one. Boxes you add are recorded as reviewed by you, Mora, with the time and this image's revision.</p>
      <dl class="facts">
        <dt>Box</dt><dd class="mono">1052, 250 → 1192, 520</dd>
        <dt>Size</dt><dd>140 × 270 px</dd>
        <dt>At 320</dt><dd>23 × 45 px</dd>
        <dt>Edge</dt><dd>Not clipped</dd>
      </dl>
    </section>
    <section class="sec" aria-labelledby="how-h">
      <h2 id="how-h">Drawing</h2>
      <ul class="checks small t2" role="list">
        <li>${I.box}<span>Drag from corner to corner. Box the visible part only.</span></li>
        <li>${I.key}<span>Arrow keys move the box 1 px; with Shift, 10 px. Alt with arrows resizes.</span></li>
        <li>${I.zoom}<span>Press Z to zoom to the box, 0 to fit the image.</span></li>
      </ul>
    </section>
  </div>
  ${dock({ left: "5 of 15 to decide", note: "This image" })}
</aside>`;
  const body = `${header("review")}
<main id="main">
${toolbar({ tool: "box" })}
<div class="review">
  <div class="stage">
    <h1 class="sr">Review IMG_0412.png: adding a box</h1>
    ${compactNote}
    ${canvas(OBJECTS, { drawing: draw, extra: pop })}
    ${legend(OBJECTS)}
    ${strip(OBJECTS, null)}
  </div>
  ${inspector}
</div></main>
${footerSlim}`;
  out("review-draw.html", page("Add a box · IMG_0412.png", body, { status: "Box tool. Draw a box, then pick its class." }));
}

// R3: audit sample, blind first. Only the reviewer's own boxes show.
function r3() {
  const mine = [
    [212, 250, 352, 520],
    [372, 250, 512, 520],
    [532, 250, 672, 520],
    [692, 250, 832, 520],
    [852, 248, 993, 521],
    [212, 610, 352, 880],
    [372, 610, 512, 880],
    [900, 610, 1040, 880],
    [1060, 610, 1200, 880],
  ].map((box, i) => ({ id: i + 1, cls: ["oat", "oat", "soy", "almond", "almond", "almond", "almond", "soy", "soy"][i], box, tier: "reviewed" }));
  const inspector = `<aside class="inspector" aria-label="Audit">
  <div class="scroll">
    <section class="sec" aria-labelledby="aud-h">
      <div class="objhead"><h2 id="aud-h">Audit sample</h2><span class="tag warn">Blind first</span></div>
      <p class="small t2">This image was picked at random from auto-accepted work. Box every in-scope object you can see. Proposals stay hidden until you compare, so the models can't steer you.</p>
      <dl class="facts">
        <dt>Your boxes</dt><dd>9</dd>
        <dt>Scope</dt><dd>Plant milk 1 L, price tags</dd>
        <dt>Picked</dt><dd>1 of 20 this week</dd>
      </dl>
    </section>
    <section class="sec" aria-labelledby="aud2-h">
      <h2 id="aud2-h">What happens when you compare</h2>
      <ul class="checks small t2" role="list">
        <li>${I.check}<span>Your boxes are saved first, as they are. Comparing never edits them.</span></li>
        <li>${I.check}<span>Then labelloop shows each machine label next to yours: matched, missed by the machine, or extra.</span></li>
        <li>${I.check}<span>Disagreements count against the auto-accept policy that produced this image.</span></li>
      </ul>
    </section>
  </div>
  <div class="dock">
    <div class="label" style="display:flex;justify-content:space-between"><span>Audit</span><span class="num">9 boxes so far</span></div>
    <div class="row"><button class="pill md">Compare with proposals</button></div>
    <p class="help">Only when you've boxed everything you can see.</p>
  </div>
</aside>`;
  const body = `${header("review")}
<main id="main">
${toolbar({ tool: "box", audit: true, file: "IMG_0388.png", pos: "3 of 20" })}
<h1 class="sr">Audit IMG_0388.png</h1>
<div class="review">
  <div class="stage hide-m">
    ${canvas(mine, { alt: "Shelf photo, store 14 aisle 3, with your 9 boxes." })}
    ${legend(mine, { hidden: true })}
  </div>
  ${inspector.replace('<aside class="inspector"', '<aside class="inspector hide-m"')}
  <section class="audit-narrow only-m" aria-label="Audit sample">
    <div class="notice" role="note">${I.info}<p>Audit samples need a screen 820 px or wider. You need to box every object yourself before you compare, and drawing needs the larger screen.</p></div>
    <div class="row"><button class="pill ghost md">Skip</button><span class="small t2">The queue won't show you audit samples on this screen.</span></div>
  </section>
</div></main>
${footerSlim}`;
  out("review-audit.html", page("Audit IMG_0388.png", body));
}

// R4: every proposal decided; one object left uncertain; ready to accept.
function r4() {
  const done = OBJECTS.map((o) => {
    if (o.tier !== "proposed") return o;
    if (o.kind === "class") return { ...o, cls: o.to, kind: null, tier: "reviewed" };
    if (o.kind === "geometry") return { ...o, box: o.toBox, kind: null, tier: "reviewed" };
    if (o.kind === "split") return { ...o, box: o.parts[0], kind: null, tier: "reviewed" };
    return { ...o, kind: null, tier: "reviewed" };
  });
  done.push({ id: 15, cls: "oat", box: [1440, 610, 1580, 880], tier: "reviewed" });
  done.push({ id: 16, cls: "oat", box: [1052, 250, 1192, 520], tier: "reviewed" });
  const c = counts(done);
  const inspector = `<aside class="inspector" aria-label="This image">
  <div class="scroll">
    <section class="sec" aria-labelledby="img-h">
      <div class="objhead"><h2 id="img-h">Ready to accept</h2></div>
      <p class="small t2">You decided every proposal. Check the list, then accept the image.</p>
      <ul class="checks" role="list">
        <li><span class="ok-i">${I.check}</span><span>5 proposals decided: 4 accepted, 1 changed by you</span></li>
        <li><span class="ok-i">${I.check}</span><span>1 box added that neither model found</span></li>
        <li><span class="ok-i">${I.check}</span><span>No in-scope object is left without a box</span></li>
        <li><span class="todo-i">${pg("uncertain")}</span><span>1 object is uncertain. It goes to adjudication, and this image stays out of releases until then.</span></li>
        <li><span class="todo-i">${pg("auto")}</span><span>6 boxes stay auto-accepted. Accepting the image does not mark them reviewed.</span></li>
      </ul>
      <button class="pill ghost" style="align-self:flex-start">${pg("reviewed")}Confirm the 6 auto-accepted boxes</button>
      <p class="help" style="margin-top:-6px">Records one review by you for each box. Only do this if you checked each one.</p>
    </section>
    <section class="sec" aria-labelledby="sum-h">
      <h2 id="sum-h">After you accept</h2>
      <dl class="facts">
        <dt>Reviewed</dt><dd>${c.reviewed} boxes · by you and Ade</dd>
        <dt>Auto-accepted</dt><dd>${c.auto} boxes · policy P-2</dd>
        <dt>Uncertain</dt><dd>${c.uncertain} box · to adjudication</dd>
        <dt>Completeness</dt><dd>Checked by Mora</dd>
      </dl>
    </section>
  </div>
  ${dock({ left: "0 of 16 to decide", note: "This image", ready: true })}
</aside>`;
  const body = `${header("review")}
<main id="main">
${toolbar()}
<div class="review">
  <div class="stage">
    <h1 class="sr">Review IMG_0412.png: ready to accept</h1>
    ${compactNote}
    ${canvas(done, { tabs: "names" })}
    ${legend(done)}
    ${strip(done, null)}
  </div>
  ${inspector}
</div></main>
${footerSlim}`;
  out("review-ready.html", page("Ready · IMG_0412.png", body));
}

r1();
r2();
r3();
r4();
console.log("review pages written");
