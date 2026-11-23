// node parity/run_js.js in.json out.json [tamper-mode] : runs the browser verifier (static/arcverify.js) under Node
const fs = require("fs"), path = require("path");
require(path.join(__dirname, "../static/noble-hashes.min.js")); require(path.join(__dirname, "../static/replay.js")); require(path.join(__dirname, "../static/arcverify.js"));
(async () => {
  let b = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
  if (process.argv[4]) b = ArcVerify.tamper(b, process.argv[4]);
  fs.writeFileSync(process.argv[3] + ".bundle.json", JSON.stringify(b));
  const r = await ArcVerify.verifyBundle(b, { loadArtifact: async (p) => new Uint8Array(fs.readFileSync(path.join(__dirname, "../data", p))) }); delete r.evidence; delete r.calls;
  fs.writeFileSync(process.argv[3], JSON.stringify(r));
})().catch((e) => { console.error(e); process.exit(1); });
