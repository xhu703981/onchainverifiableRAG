/* Shared UI: answer card, verification diagram, evidence panel, bundle upload.
   Verification runs in this page via ArcVerify (arcverify.js): no request goes to our server. */
(function (G) {
  "use strict";
  const A = () => G.ArcVerify;
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const bare = (m) => String(m || "?").split("/").pop();
  // custom glyphs: verified = filled seal with a square-capped tick; failed = hollow ring with a cross (shape, not only colour)
  const MARK = { ok: "M11.2 17.6l3.9 3.9 7.7-8.4", bad: "M12.6 12.6l8.8 8.8M21.4 12.6l-8.8 8.8" };
  const SEAL = (ok) => `<svg width="15" height="15" viewBox="0 0 34 34" aria-hidden="true"><circle cx="17" cy="17" r="15.5" fill="${ok ? "var(--seal)" : "none"}" stroke="${ok ? "var(--seal)" : "var(--fail)"}" stroke-width="${ok ? 1 : 2.4}"/><path d="${ok ? MARK.ok : MARK.bad}" fill="none" stroke="${ok ? "var(--paper)" : "var(--fail)"}" stroke-width="${ok ? 3 : 2.6}" stroke-linecap="square"/></svg>`;
  const DL = '<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="square" aria-hidden="true"><path d="M8 2.5v8M4.5 7.5L8 11l3.5-3.5M3 13.5h10"/></svg>';
  const hexSpans = (h, other) => { // group a hex string in 8s; underline characters that differ from `other`
    const s = String(h ?? ""); if (!/^0x[0-9a-f]+$/i.test(s) && !/^b[a-z2-7]+$/.test(s)) return esc(s);
    const pre = s.startsWith("0x") ? "0x" : "", body = s.slice(pre.length), ob = other ? String(other).slice(pre.length) : null; let out = esc(pre);
    for (let i = 0; i < body.length; i += 8) { let g = ""; for (let j = i; j < Math.min(i + 8, body.length); j++) g += ob && ob[j] !== body[j] ? `<b>${esc(body[j])}</b>` : esc(body[j]); out += `<span>${g}</span>`; }
    return `<span class="hx">${out}</span>`; };
  try { performance.setResourceTimingBufferSize(5000); } catch (e) {}
  // load every face up front so verifying never triggers a request to the page's own origin
  try { ["400 12px 'Plex Mono'", "500 12px 'Plex Mono'", "400 12px 'Plex Sans'", "500 12px 'Plex Sans'", "400 12px Newsreader"].forEach((f) => document.fonts.load(f)); } catch (e) {}

  function newTurn(thread, q) {
    const t = document.createElement("div"); t.className = "turn";
    t.innerHTML = `<div class="q">${esc(q)}</div><div class="body"><div class="loading"><i></i><i></i><i></i></div></div>`;
    thread.prepend(t); return t;
  }

  function render(t, b, opt = {}) {
    const idk = /^i don.?t know/i.test(b.answer || ""), body = t.querySelector(".body");
    body.innerHTML = `<p class="a ${idk ? "idk" : ""}">${esc(b.answer)}</p>
      <div class="bar">${(b.citations || []).map((c, i) => `<span class="cite" data-i="${i}">${esc(c.id)}</span>`).join("")}
        <span class="sp"></span>
        ${b.inference ? `<span class="menu"><span data-m="char">passage</span><span data-m="answer">answer</span><span data-m="prompt">prompt</span><span data-m="model">model</span></span>
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
  const strip = (s) => s.replace(/^M\d /, "");
  function build(b, v) {
    const mc = v ? v.model_call : [], tagged = (t) => mc.filter((x) => x.tag === t), inf = b.inference;
    const rec = tagged("record"), recOk = allOk(rec);
    const headOk = v ? allOk(v.header) : true;
    const cols = [[{ k: "q", lbl: "Question", sub: "Arc testnet", tags: ["net"], checks: v ? v.header : [], ok: headOk, why: "network: " + firstBad(v ? v.header : []) }]];
    const srcs = (b.citations || []).map((c, i) => { const r = v && v.citations[i], m3 = tagged("src:" + i);
      const ok = r ? r.status === "PASS" && allOk(m3) : true;
      const checks = r ? (r.status === "PASS" ? (r.notes || []).map((n) => ({ check: n, ok: true })) : r.reasons.map((x) => ({ check: x, ok: false }))).concat(m3) : [];
      return { k: "s" + i, lbl: c.id, sub: "on-chain · trusted", tags: ["src:" + i], checks, ok, src: true,
        why: `${c.id}: ` + (r && r.status !== "PASS" ? srcReason(r.reasons) : "text was not in the committed prompt") }; });
    cols.push(srcs.length ? srcs : [{ k: "s", lbl: "Sources", sub: "none cited", tags: [], checks: [], ok: true }]);
    if (inf) {
      const nodeOk = (cs) => (v ? cs.length > 0 && allOk(cs) && recOk : true);
      const pc = rec.concat(tagged("prompt")), mcK = tagged("model"), ac = tagged("answer"), md = v && v.model;
      cols.push([{ k: "p", lbl: "Prompt", sub: "block " + inf.commit_block, tags: ["record", "prompt"], checks: pc, ok: nodeOk(pc),
        why: !recOk ? "log record: " + strip(firstBad(rec)) : mc.some((x) => /^M2 sha256/.test(x.check) && !x.ok) ? "prompt: bytes differ from the hash committed on-chain" : "prompt: " + strip(firstBad(pc)) }]);
      cols.push([{ k: "m", lbl: bare(inf.model), wide: 1, sub: md ? `served ${bare(md.served)} · ${md.provider || "?"}` : "requested", tags: ["model"], rec: 1, checks: mcK, ok: nodeOk(mcK),
        why: "model: " + (md && !md.match ? `requested ${bare(md.requested)}, served ${bare(md.served)}` : mc.some((x) => /^M4 sha256/.test(x.check) && !x.ok) ? "response bytes don't match the on-chain hash" : "differs from the model committed on-chain") }]);
      cols.push([{ k: "a", lbl: "Answer", sub: "block " + inf.finalize_block, tags: ["answer"], rec: 1, checks: ac, ok: nodeOk(ac),
        why: "answer: " + (ac.some((x) => /^M4 bundle answer/.test(x.check) && !x.ok) ? "differs from the response logged on-chain" : ac.some((x) => /^M4 sha256/.test(x.check) && !x.ok) ? "response bytes don't match the on-chain hash" : strip(firstBad(ac))) }]);
    }
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
    if (n.checks.length) h += `<h4>Checks</h4>` + n.checks.map((c) => `<div class="ck ${c.ok ? "ok" : "bad"}"><i></i><span>${esc(strip(c.check))}${c.detail && (!c.ok || /^M6/.test(c.check)) ? ` <span class="d">· ${esc(c.detail)}</span>` : ""}</span></div>`).join("");
    const cmps = items.filter((x) => x.kind === "cmp" || x.kind === "fact");
    if (cmps.length) h += `<h4>Recomputed in your browser <small>against the chain</small></h4>` + cmps.map((x) => x.kind === "fact"
      ? `<div class="ev"><div class="hd">${esc(x.label)}<span class="verdict ${x.ok ? "ok" : "bad"}">${x.ok ? "confirmed" : "not confirmed"}</span></div>
         <div class="kv"><span class="k">value</span><span class="v">${hexSpans(x.value)}</span>${x.note ? `<span class="k"></span><span class="v note">${esc(x.note)}</span>` : ""}</div></div>`
      : `<div class="ev"><div class="hd">${esc(x.label)}<span class="verdict ${x.ok ? "ok" : "bad"}">${x.ok ? "≡ identical" : "≠ differs"}</span></div>
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

  // ---------- verify (in the browser) ----------
  function nodeHTML(n) { return `<div class="node wait${n.wide ? " wide" : ""}" data-k="${n.k}" title="click for evidence"><svg class="g" viewBox="0 0 34 34" aria-hidden="true"><circle class="ring" cx="17" cy="17" r="15.5"/><path class="mk"/></svg><div class="lbl">${esc(n.lbl)}</div><div class="sub">${esc(n.sub)}</div></div>`; }
  async function verify(body, b, tampered) {
    body.querySelector(".ptx").innerHTML = "";
    const vf = body.querySelector(".vf"), cols0 = build(b, null);
    vf.innerHTML = `<div class="flow">${cols0.map((c, i) => (i ? `<div class="link" data-l="${i}"></div>` : "") + `<div class="col">${c.map(nodeHTML).join("")}</div>`).join("")}</div>
      <div class="why"></div><div class="how"></div><div class="det"></div>`;
    vf.querySelector(".det").style.display = "none";
    const t0 = performance.now(); let v;
    try { v = await A().verifyBundle(b); }
    catch (e) { vf.querySelector(".why").textContent = "could not reach the Arc RPC from your browser: " + e.message;
      vf.querySelector(".how").textContent = "verify offline-of-us instead: python verify_citations.py bundle.json  (or the cast commands in the README)"; return; }
    const mine = location.origin && location.origin !== "null"
      ? performance.getEntriesByType("resource").filter((e) => e.startTime >= t0 && new URL(e.name).origin === location.origin).length : 0;
    const cols = build(b, v), byk = Object.fromEntries(cols.flat().map((n) => [n.k, n])), det = vf.querySelector(".det");
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
        if (!n.ok && !bad) bad = n; await sleep(200); }
    }
    const why = vf.querySelector(".why");
    if (bad) { why.className = "why"; why.innerHTML = SEAL(false) + `<span>${esc(bad.why)}</span>` + (tampered ? `<span class="tag">tampered ${esc(b._t || "copy")}</span>` : ""); }
    else { why.className = "why okk"; why.innerHTML = SEAL(true) + `<span>${v.all_pass ? "Verified on Arc" : "Model call verified · no sources were cited"}</span>`; }
    G.__lastVerify = v;
    vf.querySelector(".how").textContent = `Checked in your browser, directly against ${new URL(v.rpc).host}: ${v.rpc_calls} RPC calls, ${mine} to our server.`;
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

  G.VUI = { newTurn, render, verify, loadBundle, initFileInput, esc };
})(globalThis);
