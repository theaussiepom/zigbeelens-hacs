/**
 * Executable panel JS origin vectors — must agree with Core/HACS shared JSON.
 *
 * Usage: node tests/test_panel_origin_vectors.js
 */
const fs = require("fs");
const path = require("path");

// Minimal DOM stubs so the panel module can load under Node.
global.HTMLElement = class HTMLElement {};
global.customElements = {
  get() {
    return undefined;
  },
  define() {},
};

const panelPath = path.join(
  __dirname,
  "..",
  "custom_components",
  "zigbeelens",
  "panel",
  "zigbeelens-panel.js"
);
const stagedVectorsPath = path.join(
  __dirname,
  "fixtures",
  "http_origin_vectors.json"
);
const sourceVectorsPath = path.join(
  __dirname,
  "..",
  "..",
  "core",
  "tests",
  "fixtures",
  "http_origin_vectors.json"
);
const vectorsPath = fs.existsSync(stagedVectorsPath)
  ? stagedVectorsPath
  : sourceVectorsPath;

const { canonicalizeCoreOrigin } = require(panelPath);
const vectors = JSON.parse(fs.readFileSync(vectorsPath, "utf8"));

let failed = 0;
for (const case_ of vectors) {
  const got = canonicalizeCoreOrigin(case_.input);
  if (case_.reject) {
    if (got !== null) {
      console.error("FAIL reject", JSON.stringify(case_.input), "->", got);
      failed += 1;
    }
  } else if (got !== case_.canonical) {
    console.error(
      "FAIL canonical",
      JSON.stringify(case_.input),
      "expected",
      case_.canonical,
      "got",
      got
    );
    failed += 1;
  }
}

if (failed) {
  console.error(`panel origin vectors: ${failed} failure(s)`);
  process.exit(1);
}
console.log(`panel origin vectors: ${vectors.length} ok`);
