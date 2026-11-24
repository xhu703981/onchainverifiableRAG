/* NewsVerify: in-browser port of verify_news.py (scheme news-merkle-v1). Talks only to the public Arc RPC
   and the published manifest on GitHub. The M checks reuse ArcVerify (same code as the main demo). */
(function (G) {
  "use strict";
  const enc = new TextEncoder();
  const SCHEME = "news-merkle-v1";
  const META_KEYS = ["extractor_version", "fetched_at", "normalization", "outlet", "published_at", "raw_html_sha256", "title", "url", "version"];
  const toHex = (u8) => Array.from(u8, (x) => x.toString(16).padStart(2, "0")).join("");
  const fromHex = (h) => { h = h.replace(/^0x/, ""); const o = new Uint8Array(h.length / 2); for (let i = 0; i < o.length; i++) o[i] = parseInt(h.substr(2 * i, 2), 16); return o; };
  const cat = (...a) => { const o = new Uint8Array(a.reduce((s, x) => s + x.length, 0)); let p = 0; for (const x of a) { o.set(x, p); p += x.length; } return o; };
  const sha = async (u8) => new Uint8Array(await crypto.subtle.digest("SHA-256", u8));
  const hx = (u8) => "0x" + toHex(u8);
  const keccakHex = (s) => toHex(G.nobleHashes.keccak_256(enc.encode(s)));
  const SEL = {}; for (const s of ["getBatch(bytes32)", "isTrustedRoot(bytes32)", "registerBatch(bytes32,bytes32,uint32,string)"]) SEL[s] = keccakHex(s).slice(0, 8);
  if (G.ArcVerify) Object.assign(G.ArcVerify.SEL, SEL);
  const TOPIC = "0x" + keccakHex("BatchRegistered(bytes32,bytes32,address,uint32,uint256)");

  // RFC 8785 for our value types: JS string sort = UTF-16 code units; JSON.stringify string escaping = JCS
  function jcs(v) {
    if (v === null || typeof v === "boolean" || typeof v === "number" || typeof v === "string") return JSON.stringify(v);
    if (Array.isArray(v)) return "[" + v.map(jcs).join(",") + "]";
    return "{" + Object.keys(v).sort().map((k) => JSON.stringify(k) + ":" + jcs(v[k])).join(",") + "}";
  }
  const node = (l, r) => sha(cat(Uint8Array.of(1), l, r));
  async function applyProof(leaf, path) {
    let h = leaf;
    for (const step of path) { const [side, s] = step.split(":"); const sib = fromHex(s);
      if (!["L", "R"].includes(side) || sib.length !== 32) throw new Error("bad proof step");
      h = side === "L" ? await node(sib, h) : await node(h, sib); }
    return h;
  }
  async function merkleRoot(leaves) {
    let lv = leaves.slice();
    while (lv.length > 1) { const n = []; for (let i = 0; i + 1 < lv.length; i += 2) n.push(await node(lv[i], lv[i + 1])); if (lv.length % 2) n.push(lv[lv.length - 1]); lv = n; }
    return lv[0];
  }
  const articleLeaf = async (meta, ar) => sha(cat(Uint8Array.of(2), await sha(enc.encode(jcs(meta))), ar));
  const scopeHash = async (roots) => hx(await sha(cat(...[...new Set(roots.map((r) => r.toLowerCase()))].sort().map(fromHex))));
  const ts = (s) => (s ? Date.parse(s) / 1000 : 0);
  const iso = (t) => new Date(t * 1000).toISOString().replace(".000Z", "Z");

  async function verify(b, opts = {}) {
    const rpcUrl = opts.rpc || b.rpc || "https://rpc.testnet.arc.io", calls = [], evidence = {};
    let tag = "net";
    async function rpc(method, params) {
      const t0 = performance.now();
      for (let a = 0; ; a++) {
        const r = await fetch(rpcUrl, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ jsonrpc: "2.0", id: calls.length + 1, method, params }) });
        const out = r.status === 429 ? { error: { code: -32005 } } : await r.json();
        if (out.error && out.error.code === -32005 && a < 6) { await new Promise((ok) => setTimeout(ok, 400 * 2 ** a)); continue; }
        const req = { jsonrpc: "2.0", id: calls.length + 1, method, params };
        const item = { kind: "rpc", req, res: out, ms: Math.round(performance.now() - t0), cast: `cast rpc ${method} '${JSON.stringify(params)}' --raw --rpc-url ${rpcUrl}`,
          href: /Transaction/.test(method) ? `https://explorer.testnet.arc.io/tx/${params[0]}` : method === "eth_call" ? `https://explorer.testnet.arc.io/address/${params[0].to}` : null };
        calls.push(item); (evidence[tag] = evidence[tag] || []).push(item); if (opts.onCall) opts.onCall(calls.length);
        if (out.error) throw new Error(method + ": " + JSON.stringify(out.error));
        return out.result;
      }
    }
    const call = async (to, sig, arg) => fromHex(await rpc("eth_call", [{ to, data: "0x" + SEL[sig] + arg }, "latest"]));
    const N = b.news, reg = N.registry, batches = {}, manifests = {};
    const big = (u8) => { let x = 0n; for (const v of u8) x = (x << 8n) | BigInt(v); return Number(x); };
    async function batch(root) {
      if (!batches[root]) { const d = await call(reg, "getBatch(bytes32)", root.slice(2)); const w = (i) => d.slice(32 * i, 32 * i + 32);
        const off = big(w(5)), n = big(d.slice(off, off + 32));
        batches[root] = { exists: big(w(0)) === 1, manifest_hash: hx(w(1)), registrant: "0x" + toHex(w(2).slice(12)), timestamp: big(w(4)),
                          uri: new TextDecoder().decode(d.slice(off + 32, off + 32 + n)) }; }
      return batches[root];
    }
    const trusted = async (root) => big(await call(reg, "isTrustedRoot(bytes32)", root.slice(2))) === 1;
    const head = [], scope = [], cites = [];
    const chain = parseInt(await rpc("eth_chainId", []), 16);
    head.push({ check: "chain id is Arc testnet 5042002", ok: chain === 5042002 && b.chain_id === 5042002 });
    head.push({ check: "NewsRegistry has contract code", ok: !["0x", "0x0"].includes(await rpc("eth_getCode", [reg, "latest"])), detail: reg });
    head.push({ check: "Merkle scheme is news-merkle-v1", ok: N.scheme === SCHEME });
    if (head.every((x) => x.ok)) for (const [ci, c] of (b.citations || []).entries()) {
      const out = [], chk = (check, ok, detail = "") => out.push({ check, ok: ok === null ? null : !!ok, detail });
      const p = c.paragraph, a = c.article, bt = c.batch, meta = a.meta;
      const leaf = await sha(cat(Uint8Array.of(0), enc.encode(c.text)));
      chk("N1 sha256(0x00‖text) = paragraph leaf", hx(leaf) === p.leaf.toLowerCase() && hx(await sha(enc.encode(c.text))) === c.content_hash.toLowerCase());
      let ar = new Uint8Array(), br = new Uint8Array();
      try { ar = await applyProof(leaf, p.proof); } catch (e) { /* malformed */ }
      chk("N1 paragraph proof → article root", hx(ar) === a.article_root.toLowerCase());
      const keysOk = JSON.stringify(Object.keys(meta).sort()) === JSON.stringify(META_KEYS);
      chk("N2 meta has exactly the scheme's fields", keysOk);
      try { br = await applyProof(await articleLeaf(meta, ar), a.proof); } catch (e) { /* malformed */ }
      chk("N2 article leaf (URL, outlet, title, times) → batch root", keysOk && hx(br) === bt.root.toLowerCase());
      tag = "bat:" + ci;
      const root = hx(br).length === 66 ? hx(br) : bt.root.toLowerCase(), on = await batch(root);
      chk("N3 batch root registered on NewsRegistry", on.exists, root);
      if (on.exists) {
        chk("N3 on-chain manifest hash matches", on.manifest_hash === bt.manifest_hash.toLowerCase());
        chk("N3 registrant trusted now (isTrustedRoot)", await trusted(root), on.registrant);
        const t = await rpc("eth_getTransactionByHash", [bt.tx]), r = await rpc("eth_getTransactionReceipt", [bt.tx]);
        const good = !!(t && r) && (t.to || "").toLowerCase() === reg.toLowerCase() && t.input.slice(2, 10) === SEL["registerBatch(bytes32,bytes32,uint32,string)"]
          && t.input.slice(10, 74) === root.slice(2) && parseInt(r.status, 16) === 1 && t.from.toLowerCase() === on.registrant.toLowerCase()
          && r.logs.some((l) => l.address.toLowerCase() === reg.toLowerCase() && l.topics[0] === TOPIC && l.topics[1] === root);
        const bts = good ? parseInt((await rpc("eth_getBlockByNumber", [r.blockNumber, false])).timestamp, 16) : -1;
        chk("N4 registration tx is the real registerBatch, at the on-chain time", good && bts === on.timestamp, bt.tx);
        chk("N5 claimed fetched_at is not after the batch block time", ts(meta.fetched_at) > 0 && ts(meta.fetched_at) <= on.timestamp,
            `fetched ${meta.fetched_at} (crawler claim) · registered ${iso(on.timestamp)} (proven)`);
        if (opts.manifest !== false) {
          try {
            if (!(on.uri in manifests)) manifests[on.uri] = new Uint8Array(await (await fetch(on.uri)).arrayBuffer());
            const mb = manifests[on.uri], hok = hx(await sha(mb)) === on.manifest_hash;
            let listed = false, rebuilt = false;
            if (hok) { const m = JSON.parse(new TextDecoder().decode(mb));
              listed = m.articles.some((x) => x.article_root === a.article_root.toLowerCase() && jcs(x.meta) === jcs(meta));
              const leaves = []; for (const x of m.articles) leaves.push(await articleLeaf(x.meta, fromHex(x.article_root)));
              rebuilt = hx(await merkleRoot(leaves)) === root; }
            chk("N6 published manifest hashes to the on-chain value, lists this article, rebuilds the root", hok && listed && rebuilt, on.uri);
          } catch (e) { chk("N6 published manifest (fetch failed, skipped)", null, String(e.message || e)); }
        }
      }
      if (opts.outlets) chk("U1 outlet is one of your chosen sources", opts.outlets.includes(meta.outlet), meta.outlet);
      cites.push({ id: c.id, status: out.every((x) => x.ok !== false) ? "PASS" : "FAIL", checks: out, registered_at: on.exists ? on.timestamp : null });
    }
    tag = "scope";
    const roots = ((N.scope || {}).roots || []).map((r) => r.toLowerCase()), sc = (check, ok, detail = "") => scope.push({ check, ok: !!ok, detail });
    if (head.every((x) => x.ok)) {
      sc("S1 scope hash = sha256(sorted batch roots)", roots.length && (await scopeHash(roots)) === (N.scope.scope_hash || "").toLowerCase(), `${roots.length} batch roots`);
      sc("S1 every cited batch is inside the scope", (b.citations || []).every((c) => roots.includes(c.batch.root.toLowerCase())));
      let allT = roots.length > 0, mx = 0;
      for (const r of roots) { const i = await batch(r); allT = allT && i.exists && (await trusted(r)); mx = Math.max(mx, i.timestamp); }
      sc("S2 every scope batch is trusted now", allT);
      sc("S2 as of = latest on-chain registration time in the scope", ts(b.as_of) === mx, `${b.as_of} vs ${mx ? iso(mx) : "-"}`);
      sc("S3 sha256(retrieval config) = config hash", hx(await sha(enc.encode(N.config_json))) === N.config_hash.toLowerCase());
      if (b.inference) { let sys = ""; try { sys = JSON.parse(b.inference.request_body).messages.find((m) => m.role === "system").content; } catch (e) { /* */ }
        sc("S4 scope, config and as-of are inside the committed request",
           [`news_registry=${reg}`, `scheme=${SCHEME}`, `news_scope=${N.scope.scope_hash}`, `retrieval_config=${N.config_hash}`, `as_of=${b.as_of}`].every((s) => sys.includes(s)),
           "fixed on-chain before the model call"); }
    }
    let model = { model_call: [] };
    if (b.inference && head.every((x) => x.ok)) model = await G.ArcVerify.verifyBundle({ ...b, contract: b.inference.log_contract }, { modelOnly: true, rpc: rpcUrl, onCall: opts.onCall });
    const flat = [...head, ...scope, ...cites.flatMap((c) => c.checks), ...model.model_call];
    const ev = { ...(model.evidence || {}) }; for (const k in evidence) ev[k] = (ev[k] || []).concat(evidence[k]);
    return { all_pass: flat.every((x) => x.ok !== false), header: head, citations: cites, scope, model_call: model.model_call, model: model.model || null,
             rpc: rpcUrl, evidence: ev, rpc_calls: calls.length + (model.rpc_calls || 0) };
  }

  function tamper(b, mode) {
    const t = JSON.parse(JSON.stringify(b)), c = t.citations[0];
    if (mode === "text") { const i = Math.floor(c.text.length / 2); c.text = c.text.slice(0, i) + (c.text[i] === "x" ? "y" : "x") + c.text.slice(i + 1); t._t = `one character of ${c.id}`; }
    else if (mode === "url") { c.article.meta.url = "https://example.com/not-the-real-article"; t._t = `the URL of ${c.id}`; }
    else if (mode === "answer") { t.answer = t.answer + " (edited)"; t._t = "the answer"; }
    else if (mode === "time") { c.article.meta.published_at = "2020-01-01T00:00:00Z"; t._t = `the publish time of ${c.id}`; }
    return t;
  }
  G.NewsVerify = { verify, tamper, jcs };
})(globalThis);
