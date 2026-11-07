// Invented fixture: a 1920 x 1080 retail-shelf frame drawn as SVG (licence-clean), and the
// objects on it in canonical source pixels [x_min, y_min, x_max, y_max]. The scene's colours are
// image content, not UI colours, so they are raw hex on purpose.
const W = 1920;
const H = 1080;

const CLASSES = {
  oat: { name: "Oat milk 1 L", short: "Oat milk", band: "#c99a52", word: "OAT" },
  soy: { name: "Soy milk 1 L", short: "Soy milk", band: "#4f8a4b", word: "SOY" },
  almond: { name: "Almond milk 1 L", short: "Almond milk", band: "#b8643a", word: "ALMOND" },
  tag: { name: "Price tag", short: "Price tag" },
};

// tier: proposed | auto | reviewed | uncertain. kind: what the open proposal is, if any.
const OBJECTS = [
  { id: 1, cls: "oat", box: [212, 250, 352, 520], tier: "reviewed", by: "Mora" },
  { id: 2, cls: "oat", box: [372, 250, 512, 520], tier: "auto" },
  { id: 3, cls: "soy", box: [532, 250, 672, 520], tier: "auto" },
  {
    id: 4,
    cls: "soy",
    box: [692, 250, 832, 520],
    tier: "proposed",
    kind: "class",
    to: "almond",
  },
  {
    id: 5,
    cls: "almond",
    box: [862, 288, 982, 520],
    tier: "proposed",
    kind: "geometry",
    toBox: [852, 248, 993, 521],
  },
  { id: 6, cls: "tag", box: [560, 530, 602, 550], tier: "proposed", kind: "new", tiny: true },
  { id: 7, cls: "tag", box: [880, 530, 922, 550], tier: "auto", tiny: true },
  { id: 8, cls: "almond", box: [212, 610, 352, 880], tier: "reviewed", by: "Ade" },
  { id: 9, cls: "almond", box: [372, 610, 512, 880], tier: "auto" },
  { id: 10, cls: null, box: [560, 768, 832, 880], tier: "uncertain", why: "Label faces away" },
  { id: 11, cls: "soy", box: [900, 610, 1040, 880], tier: "auto" },
  { id: 12, cls: "soy", box: [1060, 610, 1200, 880], tier: "auto" },
  {
    id: 13,
    cls: "oat",
    box: [1300, 610, 1580, 880],
    tier: "proposed",
    kind: "split",
    parts: [
      [1300, 610, 1440, 880],
      [1440, 610, 1580, 880],
    ],
  },
  { id: 14, cls: "soy", box: [1846, 610, 1920, 880], tier: "proposed", kind: "new", clipped: true },
];

const carton = (x, y, w, h, c, extra = "") => {
  const k = CLASSES[c];
  const gable = Math.round(h * 0.09);
  return `<g ${extra}>
  <path d="M${x} ${y + gable}L${x + w * 0.18} ${y}H${x + w * 0.82}L${x + w} ${y + gable}Z" fill="#e9e4dc"/>
  <rect x="${x}" y="${y + gable}" width="${w}" height="${h - gable}" fill="#f2eee7"/>
  <rect x="${x + w - 10}" y="${y + gable}" width="10" height="${h - gable}" fill="#000" opacity=".08"/>
  <rect x="${x}" y="${y + h * 0.38}" width="${w}" height="${h * 0.3}" fill="${k.band}"/>
  <text x="${x + w / 2}" y="${y + h * 0.56}" font-family="Mona Sans, Arial, sans-serif" font-weight="800" font-size="${k.word.length > 4 ? 22 : 30}" fill="#fff" text-anchor="middle">${k.word}</text>
  <text x="${x + w / 2}" y="${y + h * 0.84}" font-family="Mona Sans, Arial, sans-serif" font-size="15" fill="#6b6259" text-anchor="middle">1 L · plant drink</text>
</g>`;
};

function sceneSvg() {
  const parts = [];
  parts.push(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">
<defs>
  <linearGradient id="wall" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#3b3936"/><stop offset="1" stop-color="#57534d"/></linearGradient>
  <linearGradient id="board" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#cfc8bd"/><stop offset="1" stop-color="#8f877c"/></linearGradient>
</defs>
<rect width="${W}" height="${H}" fill="url(#wall)"/>
<rect x="0" y="0" width="${W}" height="190" fill="#2b2a28"/>
<rect x="140" y="226" width="${W - 140}" height="300" fill="#4a4743"/>
<rect x="140" y="586" width="${W - 140}" height="300" fill="#4a4743"/>
<rect x="0" y="908" width="${W}" height="${H - 908}" fill="#6f6a62"/>
<rect x="0" y="0" width="140" height="${H}" fill="#2f2d2a"/>`);
  // Upper shelf
  parts.push(carton(212, 250, 140, 270, "oat"));
  parts.push(carton(372, 250, 140, 270, "oat"));
  parts.push(carton(532, 250, 140, 270, "soy"));
  parts.push(carton(692, 250, 140, 270, "almond"));
  parts.push(carton(852, 248, 141, 273, "almond"));
  parts.push(carton(1052, 250, 140, 270, "oat", 'opacity=".35"'));
  parts.push(`<rect x="140" y="520" width="${W - 140}" height="28" fill="url(#board)"/>`);
  for (const x of [560, 880])
    parts.push(
      `<rect x="${x}" y="530" width="42" height="20" rx="2" fill="#fbfaf6"/><text x="${x + 21}" y="545" font-family="Arial" font-size="13" font-weight="700" fill="#222" text-anchor="middle">1.89</text>`,
    );
  // Lower shelf
  parts.push(carton(212, 610, 140, 270, "almond"));
  parts.push(carton(372, 610, 140, 270, "almond"));
  // A carton on its side, label facing away
  parts.push(`<g><rect x="560" y="790" width="272" height="90" fill="#efebe4"/><path d="M832 790l-28-22H588l-28 22z" fill="#ddd7cd"/><rect x="560" y="790" width="272" height="90" fill="#000" opacity=".06"/></g>`);
  parts.push(carton(900, 610, 140, 270, "soy"));
  parts.push(carton(1060, 610, 140, 270, "soy"));
  // Multipack: two oat cartons under shrink wrap
  parts.push(carton(1300, 610, 140, 270, "oat"));
  parts.push(carton(1440, 610, 140, 270, "oat"));
  parts.push(`<rect x="1296" y="640" width="288" height="200" rx="10" fill="#fff" opacity=".18"/>`);
  // Clipped at the right edge
  parts.push(carton(1846, 610, 140, 270, "soy"));
  parts.push(`<rect x="140" y="880" width="${W - 140}" height="28" fill="url(#board)"/>`);
  parts.push("</svg>");
  return parts.join("\n");
}

// A small standalone carton for reference-bank thumbnails (front, angled, low light).
function refSvg(cls, variant = 0) {
  const tint = ["#3f3c38", "#2a2826", "#56524c"][variant % 3];
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 180 320"><rect width="180" height="320" fill="${tint}"/>${carton(20, 25, 140, 270, cls, variant === 1 ? 'transform="rotate(-6 90 160)"' : "")}</svg>`;
}

module.exports = { W, H, CLASSES, OBJECTS, sceneSvg, refSvg };
