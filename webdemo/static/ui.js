/* Shared UI: answer card, verification diagram, evidence panel, bundle upload.
   Verification runs in this page via ArcVerify (arcverify.js): no request goes to our server. */
(function (G) {
  "use strict";
  const A = () => G.ArcVerify;
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  // display-only: passage ids P1, P3… are shown as "Source 1", "Source 3". Bundles, prompts and hashes keep the P ids.
  const disp = (x) => String(x ?? "").replace(/\bP(\d+)\b/g, "Source $1");
  const dispAnswer = (x) => String(x ?? "").replace(/\[P(\d+)\]/g, "[Source $1]");
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const bare = (m) => String(m || "?").split("/").pop();
  // custom glyphs: verified = filled seal with a square-capped tick; failed = hollow ring with a cross (shape, not only colour)
  // chain-link glyphs: verified = closed, filled link with a tick; failed = link broken open (gap) with a cross. Shape, not only colour.
  const MARK = { ok: "M16 14.5l4.5 4.5 9.5-10", bad: "M18.5 9.5l9 9M27.5 9.5l-9 9" };
  const LINK = (cls) => `<svg class="g" viewBox="0 0 46 28" aria-hidden="true"><rect class="ring" x="1" y="1" width="44" height="26" rx="13"/><rect class="gap" x="5" y="-1" width="5" height="30"/><path class="mk"${cls ? ` d="${MARK[cls]}"` : ""}/></svg>`;
  const SEAL = (ok) => `<svg width="26" height="16" viewBox="0 0 46 28" aria-hidden="true"><rect x="1.5" y="1.5" width="43" height="25" rx="12.5" fill="${ok ? "var(--cobalt)" : "var(--void-wash)"}" stroke="${ok ? "var(--cobalt)" : "var(--void)"}" stroke-width="3"/>${ok ? "" : `<rect x="5" y="-1" width="5" height="30" fill="var(--paper)"/>`}<path d="${ok ? MARK.ok : MARK.bad}" fill="none" stroke="${ok ? "#fff" : "var(--void)"}" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  const DL = '<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="square" aria-hidden="true"><path d="M8 2.5v8M4.5 7.5L8 11l3.5-3.5M3 13.5h10"/></svg>';
  const hexSpans = (h, other) => { // group a hex string in 8s; underline characters that differ from `other`
    const s = String(h ?? ""); if (!/^0x[0-9a-f]+$/i.test(s) && !/^b[a-z2-7]+$/.test(s)) return esc(s);
    const pre = s.startsWith("0x") ? "0x" : "", body = s.slice(pre.length), ob = other ? String(other).slice(pre.length) : null; let out = esc(pre);
    for (let i = 0; i < body.length; i += 8) { let g = ""; for (let j = i; j < Math.min(i + 8, body.length); j++) g += ob && ob[j] !== body[j] ? `<b>${esc(body[j])}</b>` : esc(body[j]); out += `<span>${g}</span>`; }
    return `<span class="hx">${out}</span>`; };
  try { performance.setResourceTimingBufferSize(5000); } catch (e) {}
  // load every face up front so verifying never triggers a request to the page's own origin
  try { ["400 12px 'Atkinson Next'", "700 12px 'Atkinson Next'", "400 12px 'Atkinson Mono'"].forEach((f) => document.fonts.load(f)); } catch (e) {}

  function newTurn(thread, q) {
    const t = document.createElement("div"); t.className = "turn";
    t.innerHTML = `<div class="q">${esc(q)}</div><div class="body"><div class="loading"><i></i><i></i><i></i></div></div>`;
    thread.prepend(t); return t;
  }

  function render(t, b, opt = {}) {
    const idk = /^i don.?t know/i.test(b.answer || ""), body = t.querySelector(".body");
    body.innerHTML = `<p class="a ${idk ? "idk" : ""}">${esc(dispAnswer(b.answer))}</p>
      <div class="bar">${(b.citations || []).map((c, i) => `<span class="cite" data-i="${i}">${esc(disp(c.id))}</span>`).join("")}
        <span class="sp"></span>
        ${b.inference ? `<span class="menu"><span data-m="char">passage</span><span data-m="answer">answer</span><span data-m="prompt">prompt</span><span data-m="model">model</span>${b.retrieval ? `<span data-m="hide">hide a document</span>` : ""}</span>
        <button class="icon" data-a="tamper" title="Tamper with a copy, then verify it">tamper</button>` : ""}
        ${opt.download === false ? "" : `<button class="icon" data-a="dl" title="Download bundle" aria-label="Download bundle">${DL}</button>`}
        <button class="btn" data-a="verify">Verify</button></div>
      <div class="ptx"></div><div class="vf"></div>`;
    body.querySelectorAll(".cite").forEach((el) => (el.onclick = () => {
      const on = el.classList.toggle("on"); body.querySelectorAll(".cite").forEach((o) => o !== el && o.classList.remove("on"));
      body.querySelector(".ptx").innerHTML = on ? `<div class="ptext">${esc(b.citations[+el.dataset.i].text)}</div>` : ""; }));
    const dl = body.querySelector("[data-a=dl]");
    if (dl) dl.onclick = () => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([JSON.stringify(b, null, 1)], { type: "application/json" }));
      a.download = `bundle_${(b.inference ? b.inference.answer_id.slice(2, 12) : "answer")}.json`; a.click(); };
    body.querySelector("[data-a=verify]").onclick = () => verify(body, b, false);
    const tb = body.querySelector("[data-a=tamper]");
    if (tb) tb.onclick = () => body.querySelector(".menu").classList.toggle("on");
    body.querySelectorAll("[data-m]").forEach((el) => (el.onclick = () => { body.querySelector(".menu").classList.remove("on"); verify(body, A().tamper(b, el.dataset.m), true); }));
    return body;
  }

  // ---------- map verifier output to diagram nodes ----------
  function srcReason(rs) { const r = (rs || []).join(" ");
    if (/altered/.test(r)) return "text was changed: its hash no longer matches the chain";
    if (/NOT registered/i.test(r)) return "not registered on-chain";
    if (/trust/i.test(r)) return "publisher is not trusted";
    return (rs && rs[0]) || "source check failed"; }
  const allOk = (a) => a.every((x) => x.ok);
  const firstBad = (a) => (a.find((x) => !x.ok) || {}).check || "";
  const strip = (s) => s.replace(/^[MCNSU]\d /, "");
  function build(b, v) {
    const mc = v ? v.model_call : [], tagged = (t) => mc.filter((x) => x.tag === t), inf = b.inference;
    const rec = tagged("record"), recOk = allOk(rec);
    const headOk = v ? allOk(v.header) : true;
    const cols = [];
    if (b.retrieval) { const cc = v ? v.corpus || [] : [], R = b.retrieval, f = (re) => cc.find((x) => re.test(x.check) && x.ok === false);
      const skipped = cc.some((x) => x.ok === null), rp = f(/^C5 replaying/);
      const why = rp ? (/omits/.test(rp.detail) ? "corpus: replaying retrieval finds a document the answer's ranking left out" : "corpus: replaying retrieval gives a different ranking")
        : f(/^C3 every ranked/) ? "corpus: a candidate is not in the committed corpus"
        : f(/^C4/) ? "corpus: a trusted candidate was skipped when building the prompt"
        : f(/^C2 corpus/) ? "corpus: the ranking differs from the one committed before the model call"
        : f(/^C5 fetched/) ? "corpus: the fetched files don't match the on-chain commitment"
        : "corpus: " + strip(firstBad(cc));
      cols.push([{ k: "c", lbl: "Corpus", sub: `${Number(R.leaf_count).toLocaleString("en-US")} passages`, tags: ["corpus"], checks: cc,
        ok: v ? cc.length > 0 && cc.every((x) => x.ok !== false) : true, why, skipped }]); }
    cols.push([{ k: "q", lbl: "Question", sub: "Arc testnet", tags: ["net"], checks: v ? v.header : [], ok: headOk, why: "network: " + firstBad(v ? v.header : []) }]);
    const srcs = (b.citations || []).map((c, i) => { const r = v && v.citations[i], m3 = tagged("src:" + i);
      const ok = r ? r.status === "PASS" && allOk(m3) : true;
      const checks = r ? (r.status === "PASS" ? (r.notes || []).map((n) => ({ check: n, ok: true })) : r.reasons.map((x) => ({ check: x, ok: false }))).concat(m3) : [];
      return { k: "s" + i, lbl: disp(c.id), sub: "trusted source", tags: ["src:" + i], checks, ok, src: true,
        why: `${c.id}: ` + (r && r.status !== "PASS" ? srcReason(r.reasons) : "text was not in the committed prompt") }; });
    cols.push(srcs.length ? srcs : [{ k: "s", lbl: "Sources", sub: "none cited", tags: [], checks: [], ok: true }]);
    if (inf) {
      const nodeOk = (cs) => (v ? cs.length > 0 && allOk(cs) && recOk : true);
      const pc = rec.concat(tagged("prompt")), mcK = tagged("model"), ac = tagged("answer"), md = v && v.model;
      cols.push([{ k: "p", lbl: "Prompt", sub: "block " + inf.commit_block, tags: ["record", "prompt"], checks: pc, ok: nodeOk(pc),
        why: !recOk ? "log record: " + strip(firstBad(rec)) : mc.some((x) => /^M2 sha256/.test(x.check) && !x.ok) ? "prompt: bytes differ from the hash committed on-chain" : "prompt: " + strip(firstBad(pc)) }]);
      cols.push([{ k: "m", lbl: bare(inf.model), wide: 1, sub: md ? `served by ${md.provider || "unknown"}` : "requested", tags: ["model"], rec: 1, checks: mcK, ok: nodeOk(mcK),
        why: "model: " + (md && !md.match ? `requested ${bare(md.requested)}, served ${bare(md.served)}` : mc.some((x) => /^M4 sha256/.test(x.check) && !x.ok) ? "response bytes don't match the on-chain hash" : "differs from the model committed on-chain") }]);
      cols.push([{ k: "a", lbl: "Answer", sub: "block " + inf.finalize_block, tags: ["answer"], rec: 1, checks: ac, ok: nodeOk(ac),
        why: "answer: " + (ac.some((x) => /^M4 bundle answer/.test(x.check) && !x.ok) ? "differs from the response logged on-chain" : ac.some((x) => /^M4 sha256/.test(x.check) && !x.ok) ? "response bytes don't match the on-chain hash" : strip(firstBad(ac))) }]);
    }
    return cols;
  }

  // ---------- news bundles: paragraph -> article -> batch -> prompt -> model -> answer ----------
  function buildNews(b, v) {
    const mc = v ? v.model_call : [], tagged = (t) => mc.filter((x) => x.tag === t), inf = b.inference, cites = b.citations || [];
    const rec = tagged("record"), recOk = allOk(rec);
    const okc = (cs) => (v ? cs.length > 0 && cs.every((x) => x.ok !== false) : true);
    const grp = (i, re) => ((v && v.citations[i]) ? v.citations[i].checks : []).filter((x) => re.test(x.check));
    const bad = (cs, re) => cs.some((x) => re.test(x.check) && x.ok === false);
    const head = v ? v.header : [], cols = []; // chain id, registry code and scheme sit on the batch nodes: they are facts about the registry
    if (cites.length) {
      cols.push(cites.map((c, i) => { const cs = grp(i, /^N1/);
        return { k: "s" + i, lbl: disp(c.id), sub: "paragraph", tags: ["src:" + i], checks: cs, ok: okc(cs), src: true,
          why: `${c.id}: the text was changed, so its hash no longer reaches the article root` }; }));
      cols.push(cites.map((c, i) => { const cs = grp(i, /^(N2|N5|U1)/);
        return { k: "r" + i, lbl: c.article.meta.outlet, sub: "article", tags: ["art:" + i], checks: cs, ok: okc(cs),
          why: `${c.id}: ` + (bad(cs, /^U1/) ? "this outlet is not one of your chosen sources" : bad(cs, /^N5/) ? "the claimed fetch time is after the registration"
            : "the URL, outlet, title or times differ from the registered article") }; }));
      cols.push(cites.map((c, i) => { const cs = head.concat(grp(i, /^(N3|N4|N6)/));
        return { k: "b" + i, lbl: "Batch", sub: c.batch.root.slice(0, 10) + "…", tags: ["net", "bat:" + i], checks: cs, ok: okc(cs),
          why: !allOk(head) ? "registry: " + firstBad(head) : `${c.id}: ` + (bad(cs, /^N3 batch root registered/) ? "this batch root is not registered on NewsRegistry" : bad(cs, /^N3 registrant/) ? "the batch was registered by a key that is not trusted"
            : bad(cs, /^N4/) ? "the registration transaction does not match" : bad(cs, /^N6/) ? "the published manifest does not match the chain" : strip(firstBad(cs))) }; }));
    } else cols.push([{ k: "s", lbl: "Sources", sub: "none cited", tags: ["net"], checks: head, ok: v ? allOk(head) : true, why: "registry: " + firstBad(head) }]);
    const sc = v ? v.scope : [];
    if (inf) {
      const nodeOk = (cs) => (v ? cs.length > 0 && cs.every((x) => x.ok !== false) && recOk : true);
      const pc = rec.concat(tagged("prompt"), ...cites.map((c, i) => tagged("src:" + i)), sc), mcK = tagged("model"), ac = tagged("answer"), md = v && v.model;
      cols.push([{ k: "p", lbl: "Prompt", sub: "block " + inf.commit_block, tags: ["record", "prompt", "scope"], checks: pc, ok: nodeOk(pc),
        why: !recOk ? "log record: " + strip(firstBad(rec)) : pc.some((x) => /^M3 cited/.test(x.check) && !x.ok) ? "prompt: a cited paragraph was not in the prompt committed on-chain"
          : pc.some((x) => /^M2 sha256/.test(x.check) && !x.ok) ? "prompt: bytes differ from the hash committed on-chain" : "prompt: " + strip(firstBad(pc)) }]);
      cols.push([{ k: "m", lbl: bare(inf.model), wide: 1, sub: md ? `served by ${md.provider || "unknown"}` : "requested", tags: ["model"], rec: 1, checks: mcK, ok: nodeOk(mcK),
        why: "model: " + (md && !md.match ? `requested ${bare(md.requested)}, served ${bare(md.served)}` : "response bytes don't match the on-chain hash") }]);
      cols.push([{ k: "a", lbl: "Answer", sub: "block " + inf.finalize_block, tags: ["answer"], rec: 1, checks: ac, ok: nodeOk(ac),
        why: "answer: " + (ac.some((x) => /^M4 bundle answer/.test(x.check) && !x.ok) ? "differs from the response logged on-chain" : strip(firstBad(ac))) }]);
    } else cols.push([{ k: "p", lbl: "Scope", sub: "batches searched", tags: ["scope"], checks: sc, ok: okc(sc), why: "scope: " + strip(firstBad(sc)) }]);
    return cols;
  }

  // ---------- evidence panel ----------
  const SEL2SIG = () => { const m = {}; for (const s in A().SEL) m[A().SEL[s]] = s; return m; };
  function rpcTitle(it) {
    const m = it.req.method;
    if (m === "eth_call") { const d = it.req.params[0].data.slice(2); return [m, SEL2SIG()[d.slice(0, 8)] || "0x" + d.slice(0, 8)]; }
    const p = it.req.params[0]; return [m, typeof p === "string" ? (p.length > 20 ? p.slice(0, 12) + "…" + p.slice(-6) : p) : ""];
  }
  function copyBtn(text) { return `<button class="copy" data-copy="${esc(text)}">copy</button>`; }
  const cmdRow = (cmd) => `<div class="cmdline"><code>${esc(cmd)}</code>${copyBtn(cmd)}</div>`;
  function evidenceHTML(n, v) {
    const ev = v.evidence || {}, items = [].concat(...n.tags.map((t) => ev[t] || []));
    if (n.rec) items.unshift(...(ev.record || []).filter((x) => x.kind === "rpc" && /213681cd/.test(JSON.stringify(x.req.params))));
    let h = "";
    if (n.checks.length) h += `<h4>Checks</h4>` + n.checks.map((c) => `<div class="ck ${c.ok === null ? "skip" : c.ok ? "ok" : "bad"}"><i></i><span>${esc(disp(strip(c.check)))}${c.detail && (!c.ok || /^M6|^C[1345]/.test(c.check)) ? `<span class="d">${esc(disp(c.detail))}</span>` : ""}</span></div>`).join("");
    if (n.k === "c") h += corpusHTML(items, n);
    const cmps = items.filter((x) => x.kind === "cmp" || x.kind === "fact");
    if (cmps.length) h += `<h4>Recomputed in your browser <small>against the chain</small></h4>` + cmps.map((x) => x.kind === "fact"
      ? `<div class="ev"><div class="hd">${esc(disp(x.label))}<span class="verdict ${x.ok ? "ok" : "bad"}">${x.ok ? "confirmed" : "not confirmed"}</span></div>
         <div class="kv"><span class="k">value</span><span class="v">${hexSpans(x.value)}</span>${x.note ? `<span class="k"></span><span class="v note">${esc(x.note)}</span>` : ""}</div></div>`
      : `<div class="ev"><div class="hd">${esc(disp(x.label))}<span class="verdict ${x.ok ? "ok" : "bad"}">${x.ok ? "≡ identical" : "≠ differs"}</span></div>
         <div class="kv"><span class="k">local</span><span class="v">${hexSpans(x.local, x.chain)}</span>
         <span class="k">${/vs bundle/.test(x.label) ? "bundle" : "on-chain"}</span><span class="v">${hexSpans(x.chain, x.local)}</span>
         ${x.cmd ? `<span class="k">shell</span><span class="v">${cmdRow(x.cmd)}</span>` : ""}</div></div>`).join("");
    const rpcs = items.filter((x) => x.kind === "rpc");
    if (rpcs.length) h += `<h4>Raw RPC <small>direct to ${esc(new URL(v.rpc).host)}</small></h4><div class="rpcs">` + rpcs.map((x) => {
        const [m, sig] = rpcTitle(x);
        return `<details class="rpc"><summary><span class="n">${String(x.req.id).padStart(2, "0")}</span><span class="m">${esc(m)}</span><span class="s">${esc(sig)}</span><span class="ms">${x.ms} ms</span><span class="t"></span></summary>
        <div class="x"><span class="k">request</span><pre>${esc(JSON.stringify(x.req, null, 2))}</pre>
        <span class="k">response</span><pre>${esc(JSON.stringify(x.res, null, 2))}</pre>
        <span class="k">reproduce</span><div class="cast">${cmdRow(x.cast)}</div>
        ${x.href ? `<span class="k">explorer</span><span><a target="_blank" rel="noopener" href="${esc(x.href)}">${esc(x.href.replace(/^https:\/\//, "").replace(/(0x[0-9a-fA-F]{8})[0-9a-fA-F]{24,}/, "$1…"))} ↗</a></span>` : ""}</div></details>`; }).join("") + `</div>`;
    return h;
  }

  // ---------- corpus evidence: proof path + replay ----------
  const short = (h) => esc(String(h).slice(0, 10)) + "…" + esc(String(h).slice(-4));
  const mb = (n) => (n < 1048576 ? Math.max(1, Math.round(n / 1024)) + " KB" : (n / 1048576).toFixed(n > 1048576 * 10 ? 0 : 1) + " MB");
  function corpusHTML(items, n) {
    let h = "";
    for (const pr of items.filter((x) => x.kind === "proof")) {
      h += `<h4>Inclusion proof <small>Merkle path, recomputed in your browser</small></h4><div class="ev"><div class="hd">${esc(disp(pr.label[0].toUpperCase() + pr.label.slice(1)))}, leaf ${esc(pr.index)} of ${Number(pr.leaves).toLocaleString("en-US")}<span class="verdict ${pr.ok ? "ok" : "bad"}">${pr.ok ? "reaches the on-chain root" : "does not reach the root"}</span></div>
        <div class="kv path"><span class="k">passage</span><span class="v">${hexSpans(pr.id)}</span>` +
        (pr.steps || []).map((s, i) => s.op === "leaf" ? `<span class="k">leaf</span><span class="v">${hexSpans(s.value)}</span>`
          : `<span class="k">${s.op === "up" ? "carry ↑" : (s.op === "L" ? "⊕ left" : "⊕ right")}</span><span class="v">${s.op === "up" ? `<span class="note">odd node, promoted</span>` : hexSpans(s.sib)}</span>`).join("") +
        `<span class="k">root</span><span class="v">${hexSpans(((pr.steps || []).slice(-1)[0] || {}).value || "", pr.root)}</span><span class="k">on-chain</span><span class="v">${hexSpans(pr.root, ((pr.steps || []).slice(-1)[0] || {}).value)}</span></div></div>`;
    }
    for (const r of items.filter((x) => x.kind === "replay")) {
      h += `<h4>Replay <small>committed retriever, rerun in your browser</small></h4>`;
      if (r.error) { h += `<div class="ev"><div class="hd">could not fetch the committed corpus from ${esc(new URL(r.uri).host)}<span class="verdict bad">not run</span></div><div class="kv"><span class="k">run locally</span><span class="v">${cmdRow("python verify_citations.py bundle.json")}</span></div></div>`; continue; }
      const src = r.uri.match(/github(?:usercontent)?\.com\/([^/]+\/[^/]+)\/([0-9a-f]{7})/);
      h += `<div class="ev"><div class="kv">${(r.arts || []).map((a) => `<span class="k">${esc(a.name.replace("retriever code", "code").replace("model weights", "weights"))}</span><span class="v"><a target="_blank" rel="noopener" href="${esc(a.url)}">${esc(a.url.split("/").pop())}</a> <span class="note">${mb(a.bytes)}, sha256 ${short(a.sha256)} ${a.ok ? "≡ committed" : "≠ committed"}</span></span>`).join("")}
        <span class="k">source</span><span class="v note">${src ? `${esc(src[1])} at commit ${esc(src[2])}, pinned by the on-chain URI` : esc(r.uri)}</span></div></div>`;
      if (r.rows) { const show = r.rows.filter((x, i) => i < 8 || !x.same);
        h += `<div class="ev"><div class="hd">${r.rows.length} candidates replayed in ${r.ms} ms<span class="verdict ${r.ok ? "ok" : "bad"}">${r.ok ? "≡ identical ranking" : "≠ ranking differs"}</span></div><div class="rk">
          <span class="th">#</span><span class="th">replayed</span><span class="th">bm25 / dense</span><span class="th">in the answer</span>` +
          show.map((x) => `<span class="n">${x.rank}</span><span class="v">${short(x.id)}</span><span class="s">${x.replay.bm25 ?? "–"} / ${x.replay.dense ?? "–"}</span><span class="${x.same ? "ok" : "bad"}">${x.same ? "≡ same" : (r.missing.includes(x.id) ? "omitted" : short((x.committed || {}).id || "—"))}</span>`).join("") +
          `</div>${r.rows.length > show.length ? `<div class="more">+ ${r.rows.length - show.length} more, all identical</div>` : ""}</div>`; }
    }
    return h;
  }

  // ---------- verify (in the browser) ----------
  function nodeHTML(n) { return `<div class="node wait${n.wide ? " wide" : ""}" data-k="${n.k}" role="button" tabindex="0" title="Show on-chain evidence">${LINK()}<div class="lbl">${esc(n.lbl)}</div><div class="sub">${esc(n.sub)}</div></div>`; }
  // opt.verifyFn / opt.build: the news page passes NewsVerify.verify and buildNews; the diagram code is shared
  async function verify(body, b, tampered, opt = {}) {
    const vfn = opt.verifyFn || ((x, o) => A().verifyBundle(x, o)), bld = opt.build || build;
    body.querySelector(".ptx").innerHTML = "";
    const vf = body.querySelector(".vf"), cols0 = bld(b, null);
    vf.innerHTML = `<div class="flow">${cols0.map((c, i) => (i ? `<div class="link" data-l="${i}"></div>` : "") + `<div class="col${c.length > 1 ? " multi" : ""}" data-c="${i}">${c.map(nodeHTML).join("")}</div>`).join("")}</div>
      <div class="why"></div><div class="how"></div><div class="hint"></div><div class="det"></div>`;
    vf.querySelector(".det").style.display = "none";
    const t0 = performance.now(); let v;
    const how = vf.querySelector(".how");
    try { v = await vfn(b, { onStatus: (m) => (how.textContent = b.retrieval ? `Replaying retrieval: ${m} from the committed URI…` : "") }); }
    catch (e) { vf.querySelector(".why").textContent = "could not reach the Arc RPC from your browser: " + e.message;
      vf.querySelector(".how").textContent = "verify offline-of-us instead: python verify_citations.py bundle.json  (or the cast commands in the README)"; return; }
    const mine = location.origin && location.origin !== "null"
      ? performance.getEntriesByType("resource").filter((e) => e.startTime >= t0 && new URL(e.name).origin === location.origin).length : 0;
    const cols = bld(b, v), byk = Object.fromEntries(cols.flat().map((n) => [n.k, n])), det = vf.querySelector(".det");
    vf.querySelectorAll(".node").forEach((el) => (el.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); el.click(); } }));
    vf.querySelectorAll(".node").forEach((el) => (el.onclick = () => {
      const same = det.dataset.k === el.dataset.k && det.style.display !== "none";
      vf.querySelectorAll(".node").forEach((o) => o.classList.remove("sel"));
      if (same) { det.style.display = "none"; det.dataset.k = ""; return; }
      el.classList.add("sel"); det.dataset.k = el.dataset.k; det.style.display = ""; det.innerHTML = evidenceHTML(byk[el.dataset.k], v);
      det.querySelectorAll("[data-copy]").forEach((c) => (c.onclick = (e) => { e.preventDefault(); navigator.clipboard && navigator.clipboard.writeText(c.dataset.copy); c.textContent = "copied"; setTimeout(() => (c.textContent = "copy"), 1200); }));
    }));
    let bad = null;
    for (let i = 0; i < cols.length; i++) {
      if (i) { vf.querySelector(`[data-l="${i}"]`).classList.add(cols[i - 1].every((n) => n.ok) ? "ok" : "bad"); await sleep(300); }
      for (const n of cols[i]) { const el = vf.querySelector(`[data-k="${n.k}"]`); el.classList.remove("wait"); el.classList.add(n.ok ? "ok" : "bad");
        el.querySelector(".mk").setAttribute("d", n.ok ? MARK.ok : MARK.bad); el.setAttribute("aria-label", `${n.lbl}: ${n.ok ? "verified" : "failed"}`);
        if (!n.ok && n.src) el.querySelector(".sub").textContent = "not verified";
        if (n.k === "m") el.querySelector(".sub").textContent = n.sub;
        if (n.k === "c" && n.skipped) el.querySelector(".sub").textContent = "run replay locally";
        if (!n.ok && !bad) bad = n; await sleep(200); }
      vf.querySelector(`[data-c="${i}"]`).classList.add(cols[i].every((n) => n.ok) ? "ok" : "bad");
    }
    const why = vf.querySelector(".why");
    if (bad) { why.className = "why"; why.innerHTML = SEAL(false) + `<span>${esc(disp(bad.why))}</span>` + (tampered ? `<span class="tag">tampered ${esc(disp(b._t || "copy"))}</span>` : ""); }
    else { why.className = "why okk"; why.innerHTML = SEAL(true) + `<span>${v.all_pass ? "Verified on Arc" : "Model call verified, but no sources were cited"}</span>`; }
    vf.querySelector(".hint").textContent = "Click a node to see the on-chain evidence.";
    G.__lastVerify = v;
    vf.querySelector(".how").textContent = `Checked in your browser, directly against ${new URL(v.rpc).host}: ${v.rpc_calls} RPC calls, ${mine} to our server${b.retrieval ? `; replay files from ${(v.evidence.corpus || []).some((x) => x.kind === "replay" && x.arts) ? "raw.githubusercontent.com, sha256-checked" : "—"}` : ""}.`;
    return v;
  }

  // ---------- open a bundle file ----------
  function loadBundle(thread, file, onDone) {
    const r = new FileReader();
    r.onload = async () => { let b; const t = newTurn(thread, file.name);
      try { b = JSON.parse(r.result); if (!b.contract || !Array.isArray(b.citations)) throw new Error("not an answer bundle"); }
      catch (e) { t.querySelector(".body").innerHTML = `<div class="err">${esc(file.name)}: ${esc(e.message)}</div>`; return; }
      t.querySelector(".q").textContent = b.question || file.name;
      const body = render(t, b, { download: false }); const v = await verify(body, b, false); if (onDone) onDone(v); };
    r.readAsText(file);
  }
  function initFileInput(thread, button, dropEl) {
    const inp = document.createElement("input"); inp.type = "file"; inp.accept = ".json,application/json"; inp.style.display = "none"; document.body.appendChild(inp);
    inp.onchange = () => { if (inp.files[0]) loadBundle(thread, inp.files[0]); inp.value = ""; };
    if (button) button.onclick = () => inp.click();
    let depth = 0;
    addEventListener("dragenter", (e) => { e.preventDefault(); depth++; dropEl.classList.add("on"); });
    addEventListener("dragleave", () => { if (--depth <= 0) { depth = 0; dropEl.classList.remove("on"); } });
    addEventListener("dragover", (e) => e.preventDefault());
    addEventListener("drop", (e) => { e.preventDefault(); depth = 0; dropEl.classList.remove("on"); const f = e.dataTransfer.files[0]; if (f) loadBundle(thread, f); });
    return inp;
  }

  G.VUI = { newTurn, render, verify, buildNews, loadBundle, initFileInput, esc };
})(globalThis);
