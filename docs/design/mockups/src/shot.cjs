// Usage: node shot.cjs <page>.html <out-prefix> [tall]. Same setup as axe.cjs.
// Writes <out>-1280.png and <out>-390-full.png; with `tall`, also <out>-1280-tall.png
// (1280 x 1600) so a screen with independently scrolling panes shows its full inspector.
const REPO = process.env.REPO || require("path").resolve(__dirname, "../../../..");
const { chromium } = require(REPO + "/node_modules/playwright");
void (async () => {
  const [, , file, out, tall] = process.argv;
  const b = await chromium.launch({ executablePath: process.env.CHROMIUM || undefined });
  const shots = [
    [1280, 800, "1280", false, false],
    [390, 844, "390-full", true, true],
  ];
  if (tall) shots.push([1280, 1600, "1280-tall", false, false]);
  for (const [w, h, tag, mobile, full] of shots) {
    const ctx = await b.newContext({
      viewport: { width: w, height: h },
      deviceScaleFactor: 1,
      isMobile: mobile,
      hasTouch: mobile,
      reducedMotion: "reduce",
    });
    const p = await ctx.newPage();
    await p.goto("file://" + require("path").resolve(file));
    await p.evaluate(() => document.fonts.ready);
    const broken = await p.evaluate(() =>
      [...document.fonts].filter((f) => f.status === "error").map((f) => `${f.family} ${f.weight}`),
    );
    if (broken.length) throw new Error(`font load failed: ${broken.join(", ")}`);
    await p.waitForTimeout(250);
    await p.screenshot({ path: `${out}-${tag}.png`, fullPage: full });
    const ov = await p.evaluate(() => document.documentElement.scrollWidth - innerWidth);
    console.log(out, tag, "overflow-x", ov);
    await ctx.close();
  }
  await b.close();
})();
