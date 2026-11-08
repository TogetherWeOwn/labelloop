import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const manifest = process.argv[2]
  ? path.resolve(process.argv[2])
  : fileURLToPath(new URL("../package.json", import.meta.url));
const webRequire = createRequire(manifest);
const toolingRequire = createRequire(webRequire.resolve("typescript-eslint"));
const parserRequire = createRequire(toolingRequire.resolve("@typescript-eslint/parser"));
const estreeEntry = parserRequire.resolve("@typescript-eslint/typescript-estree");
const estree = parserRequire("@typescript-eslint/typescript-estree");
const estreeRequire = createRequire(estreeEntry);
const { minimatch } = estreeRequire("minimatch");
const matcherVersion = estreeRequire("minimatch/package.json").version;

console.log(JSON.stringify({ node: process.version, estree: estree.version, matcher: matcherVersion }));

if (process.env.EXPECTED_LINT_MATCHER) {
  test("the scoped resolution reaches the actual consumer", () => {
    assert.equal(estree.version, "8.72.1");
    assert.equal(matcherVersion, process.env.EXPECTED_LINT_MATCHER);
  });
}

const matchingCases = [
  ["tool.ts", "*.ts", true],
  ["tool.js", "*.ts", false],
  [".tool.ts", "*.ts", true],
  ["tools/tool.ts", "*.ts", false],
  ["tools/tool.ts", "tools/*.ts", true],
  ["tools/nested/tool.ts", "tools/**/*.ts", true],
  ["tool.ts", "{tool,other}.ts", true],
  ["third.ts", "{tool,other}.ts", false],
  ["tool.ts", "@(tool|other).ts", true],
  ["third.ts", "@(tool|other).ts", false],
  ["tool.ts", "!other.ts", true],
  ["other.ts", "!other.ts", false],
  ["[tool.ts", "[tool.ts", true],
  ["café.ts", "café*.ts", true],
  ["cafe.ts", "café*.ts", false],
  ["TOOL.TS", "*.ts", false],
  ["tool.ts", "", false],
];

for (const [file, pattern, expected] of matchingCases) {
  test(`consumer matcher: ${JSON.stringify(file)} against ${JSON.stringify(pattern)}`, () => {
    assert.equal(minimatch(file, pattern, { dot: true }), expected);
  });
}

test("platform-specific directory separators retain their meaning", () => {
  assert.equal(minimatch("tools/tool.ts", "tools/*.ts", { dot: true, platform: "linux" }), true);
  assert.equal(minimatch("tools\\tool.ts", "tools/*.ts", { dot: true, platform: "win32" }), true);
  assert.equal(minimatch("tools\\tool.ts", "tools/*.ts", { dot: true, platform: "linux" }), false);
});

test("invalid matcher pattern types are rejected", () => {
  assert.throws(() => minimatch("tool.ts", null, { dot: true }), TypeError);
});

const projectCases = [
  { name: "unset allowlist", patterns: undefined, file: "tool.ts", allowed: false },
  { name: "empty allowlist", patterns: [], file: "tool.ts", allowed: false },
  { name: "positive match", patterns: ["*.ts"], file: "tool.ts", allowed: true },
  { name: "negative match", patterns: ["other.ts"], file: "tool.ts", allowed: false },
  { name: "dotfile", patterns: ["*.ts"], file: ".tool.ts", allowed: true },
  { name: "directory match", patterns: ["tools/*.ts"], file: "tools/tool.ts", allowed: true },
  { name: "directory boundary", patterns: ["*.ts"], file: "tools/tool.ts", allowed: false },
  { name: "braces", patterns: ["{tool,other}.ts"], file: "tool.ts", allowed: true },
  { name: "extglob", patterns: ["@(tool|other).ts"], file: "tool.ts", allowed: true },
  { name: "negation", patterns: ["!other.ts"], file: "tool.ts", allowed: true },
  { name: "negation exclusion", patterns: ["!tool.ts"], file: "tool.ts", allowed: false },
  { name: "Unicode", patterns: ["café*.ts"], file: "café.ts", allowed: true },
  { name: "case sensitivity", patterns: ["*.ts"], file: "TOOL.TS", allowed: false },
  { name: "wide glob", patterns: ["*"], file: "tool.ts", error: /overly wide/ },
  { name: "globstar", patterns: ["**/*.ts"], file: "tool.ts", error: /disallowed/ },
  { name: "project overlap", patterns: ["included.ts"], file: "included.ts", error: /also was found/ },
];

for (const { name, patterns, file, allowed, error } of projectCases) {
  test(`project service: ${name}`, () => {
    const root = mkdtempSync(path.join(tmpdir(), "lint-consumer-"));
    try {
      writeFileSync(path.join(root, "tsconfig.json"), JSON.stringify({ include: ["included.ts"] }));
      writeFileSync(path.join(root, "included.ts"), "export const included = 1;\n");
      const filePath = path.join(root, file);
      mkdirSync(path.dirname(filePath), { recursive: true });
      const code = "export const value: number = 1;\n";
      writeFileSync(filePath, code);
      estree.clearCaches();
      const parse = () => estree.parseAndGenerateServices(code, {
        filePath,
        tsconfigRootDir: root,
        projectService: patterns === undefined ? true : { allowDefaultProject: patterns },
      });
      if (error || !allowed) {
        assert.throws(parse, error ?? /was not found by the project service/);
      } else {
        const result = parse();
        assert.ok(result.services.program?.getSourceFile(filePath));
        assert.equal(result.ast.type, "Program");
      }
    } finally {
      estree.clearCaches();
      rmSync(root, { recursive: true, force: true });
    }
  });
}
