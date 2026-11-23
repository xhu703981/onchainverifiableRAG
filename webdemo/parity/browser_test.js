// Real-browser test (headless Chrome via puppeteer-core): screenshots + network audit + in-browser parity dump.
// usage: node parity/browser_test.js http://localhost:8799 <screens dir> <out json>
const puppeteer = require(process.env.PUPPETEER || "puppeteer-core"), path = require("path"), fs = require("fs");
const [base, screens, outJson] = process.argv.slice(2);
(async () => {
  const br = await puppeteer.launch({ executablePath: "/usr/bin/google-chrome", headless: "new", args: ["--no-sandbox"] });
  const report = { pages: {}, parity: {} };
  async function open(url, w = 1100, h = 820) {
    const p = await br.newPage(); await p.setViewport({ width: w, height: h, deviceScaleFactor: 1 });
    const reqs = []; p.on("request", (r) => reqs.push({ url: r.url(), t: Date.now() }));
    await p.goto(url, { waitUntil: "networkidle0" }); return { p, reqs };
  }
  const waitDone = (p) => p.waitForFunction(() => [...document.querySelectorAll(".how")].some((e) => /in your browser|could not reach/.test(e.textContent)), { timeout: 90000 });
  const audit = (reqs, t0) => { const after = reqs.filter((r) => r.t >= t0), host = (u) => (u.startsWith("file:") ? "file" : new URL(u).host);
    const by = {}; after.forEach((r) => (by[host(r.url)] = (by[host(r.url)] || 0) + 1)); return by; };

  for (const [mode, h] of [["answer", 820], ["verify", 820], ["tamper", 820], ["model", 820], ["evidence", 1500]]) {
    const t0 = Date.now(), { p, reqs } = await open(`${base}/?demo=${mode}`, 1100, h);
    if (mode !== "answer") await waitDone(p);
    await new Promise((r) => setTimeout(r, 2200)); // let the check animation finish
    const tv = reqs.find((r) => /api\/example/.test(r.url)).t;
    report.pages[mode] = { requests_after_bundle_loaded: audit(reqs, tv + 1), how: await p.$$eval(".vf .how", (e) => e.map((x) => x.textContent).join(" | ")).catch(() => "") };
    if (mode === "evidence") await p.addStyleTag({ content: ".ask{display:none}" });
    await p.screenshot({ path: path.join(screens, mode + ".png"), fullPage: mode === "evidence" });
    if (mode === "verify") {
      // parity: run the real-browser engine on the genuine bundle and all tamper variants
      report.parity = await p.evaluate(async () => {
        const b = await (await fetch("/api/example")).json(), out = {};
        for (const m of ["", "char", "answer", "prompt", "model"]) { const x = m ? ArcVerify.tamper(b, m) : b; delete x._t;
          const r = await ArcVerify.verifyBundle(x); delete r.evidence; delete r.calls; out[m || "genuine"] = { bundle: x, result: r };
          await new Promise((ok) => setTimeout(ok, 1500)); }
        return out; });
    }
    await p.close();
  }
  // standalone verify.html from file://, upload the example bundle
  const file = "file://" + path.resolve(__dirname, "../verify.html");
  const { p, reqs } = await open(file, 1100, 820);
  const t0 = Date.now();
  const inp = await p.$('input[type=file]'); await inp.uploadFile(path.resolve(__dirname, "../data/example_bundle.json"));
  await waitDone(p); await new Promise((r) => setTimeout(r, 2200));
  report.pages.standalone = { origin: await p.evaluate(() => location.origin), requests: audit(reqs, t0), how: await p.$$eval(".vf .how, .vf .why", (e) => e.map((x) => x.textContent).join(" | ")).catch(() => ""),
    all_pass: await p.evaluate(() => window.__lastVerify && window.__lastVerify.all_pass) };
  await p.screenshot({ path: path.join(screens, "standalone.png") });
  await br.close();
  fs.writeFileSync(outJson, JSON.stringify(report));
  console.log(JSON.stringify(report.pages, null, 1));
})().catch((e) => { console.error(e); process.exit(1); });
