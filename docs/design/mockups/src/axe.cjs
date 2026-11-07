// Usage: node axe.cjs <page>.html ... (exits 1 on any violation).
// Needs playwright and axe-core: `npm i --no-save playwright axe-core` in the repo root,
// or REPO=<a checkout that has them>. CHROMIUM=<path> picks a browser binary.
const REPO = process.env.REPO || require("path").resolve(__dirname, "../../../..");
const { chromium } = require(REPO + "/node_modules/playwright");
const axeSrc = require("fs").readFileSync(REPO + "/node_modules/axe-core/axe.min.js", "utf8");
let failed = 0;
void (async () => {
  const b = await chromium.launch({
    executablePath: process.env.CHROMIUM || undefined,
  });
  for (const f of process.argv.slice(2))
    for (const w of [1280, 390]) {
      const p = await b.newPage({ viewport: { width: w, height: 844 }, reducedMotion: "reduce" });
      await p.goto("file://" + require("path").resolve(f));
      await p.evaluate(() => document.fonts.ready);
      await p.addScriptTag({ content: axeSrc });
      const r = await p.evaluate(() =>
        axe.run(document, {
          runOnly: [
            "wcag2a",
            "wcag2aa",
            "wcag21a",
            "wcag21aa",
            "wcag22a",
            "wcag22aa",
            "best-practice",
          ],
        }),
      );
      failed += r.violations.length;
      console.log(
        f,
        w,
        "violations:",
        r.violations
          .map(
            (v) =>
              `${v.id}(${v.nodes.length}): ${v.nodes
                .slice(0, 2)
                .map((n) => n.target.join(" ") + " " + (n.any[0]?.message || ""))
                .join(" | ")}`,
          )
          .join("\n   ") || "none",
      );
      await p.close();
    }
  await b.close();
  process.exit(failed ? 1 : 0);
})();
