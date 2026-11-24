/* ArcVerify: in-browser port of verify_citations.py. It talks only to a public Arc RPC.
   No server of ours is involved: hashes are recomputed locally (WebCrypto SHA-256, noble keccak-256),
   and every on-chain fact comes from JSON-RPC calls made directly from this page. */
(function (G) {
  "use strict";
  const RPC_URL = "https://rpc.testnet.arc.io";
  const CHAIN_ID = 5042002;
  const EXPLORER = "https://explorer.testnet.arc.io";
  const enc = new TextEncoder();
  const N = () => G.nobleHashes;

  // ---------- bytes / hex ----------
  const toHex = (u8) => Array.from(u8, (x) => x.toString(16).padStart(2, "0")).join("");
  const fromHex = (h) => { h = h.replace(/^0x/, ""); const o = new Uint8Array(h.length / 2); for (let i = 0; i < o.length; i++) o[i] = parseInt(h.substr(2 * i, 2), 16); return o; };
  const concat = (arrs) => { const n = arrs.reduce((s, a) => s + a.length, 0), o = new Uint8Array(n); let p = 0; for (const a of arrs) { o.set(a, p); p += a.length; } return o; };
  const b64bytes = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
  async function sha256(u8) {
    if (G.crypto && G.crypto.subtle) return "0x" + toHex(new Uint8Array(await G.crypto.subtle.digest("SHA-256", u8)));
    return "0x" + toHex(N().sha256(u8)); // non-secure context fallback (same algorithm, vendored)
  }
  const shaStr = (s) => sha256(enc.encode(s));
  const keccakHex = (s) => toHex(N().keccak_256(enc.encode(s)));

  // selectors and event topics are derived here from their signatures (not trusted constants)
  const SIGS = {
    "isVerified(bytes32)": "(bool)", "getSource(bytes32)": "(bool,string,string,address,uint256)",
    "isTrustedPublisher(address)": "(bool)", "owner()": "(address)", "registerBatch(bytes32[])": "",
    "registerSource(bytes32,string,string)": "", "isLogger(address)": "(bool)",
    "getRecord(bytes32)": "(bytes32,bytes32,bytes32,bytes32,bytes32,address,uint16,uint64,uint64,uint64,uint64)",
    "commit(bytes32,bytes32,bytes32,bytes32,bytes32,uint16)": "", "finalize(bytes32,bytes32)": "",
    "corpusInfo(uint256)": "(bytes32,bytes32,uint32,address,uint64,uint64)", "corpusUri(uint256)": "(string)",
    "isPublisher(address)": "(bool)", "areVerified(bytes32[])": "(bool[])",
  };
  const SEL = {}; for (const s in SIGS) SEL[s] = keccakHex(s).slice(0, 8);
  const SEL2SIG = {}; for (const s in SEL) SEL2SIG[SEL[s]] = s;
  const EVENTS = {
    BATCH: "BatchRegistered(address,uint256,uint256,uint256)", SINGLE: "SourceRegistered(bytes32,string,address,uint256)",
    COMMITTED: "Committed(bytes32,bytes32,bytes32,bytes32,bytes32,uint16,address,uint256)", FINALIZED: "Finalized(bytes32,bytes32,address,uint256)",
    PUBLISHED: "CorpusPublished(uint256,bytes32,bytes32,uint32,string,address)",
  };
  const TOPIC = {}; for (const k in EVENTS) TOPIC[k] = "0x" + keccakHex(EVENTS[k]);

  // ---------- ABI words ----------
  const word = (b, i) => b.slice(32 * i, 32 * i + 32);
  const asInt = (b) => { let x = 0n; for (const v of b) x = (x << 8n) | BigInt(v); return x; };
  const asAddr = (b) => "0x" + toHex(b.slice(-20));
  const dynString = (b, off) => { const n = Number(asInt(b.slice(off, off + 32))); return new TextDecoder().decode(b.slice(off + 32, off + 32 + n)); };
  const padAddr = (a) => a.toLowerCase().replace(/^0x/, "").padStart(64, "0");
  const B32 = "abcdefghijklmnopqrstuvwxyz234567";
  function cidV1Raw(digestHex) {
    const b = concat([fromHex("01551220"), fromHex(digestHex)]); let bits = 0, val = 0, out = "b";
    for (const x of b) { val = (val << 8) | x; bits += 8; while (bits >= 5) { out += B32[(val >>> (bits - 5)) & 31]; bits -= 5; } }
    if (bits > 0) out += B32[(val << (5 - bits)) & 31];
    return out;
  }
  function hashesInCalldata(inp) {
    const data = fromHex(inp), sel = toHex(data.slice(0, 4)), args = data.slice(4);
    if (sel === SEL["registerBatch(bytes32[])"]) {
      const off = Number(asInt(word(args, 0))), n = Number(asInt(args.slice(off, off + 32))), hs = [];
      for (let i = 0; i < n; i++) hs.push("0x" + toHex(args.slice(off + 32 + 32 * i, off + 64 + 32 * i)));
      return ["registerBatch", hs];
    }
    if (sel === SEL["registerSource(bytes32,string,string)"]) return ["registerSource", ["0x" + toHex(word(args, 0))]];
    return ["unknown(0x" + sel + ")", []];
  }

  // ---------- canonical JSON exactly like Python json.dumps(sort_keys, (",",":"), ensure_ascii=False) ----------
  function canonical(v) {
    if (v === null) return "null";
    if (Array.isArray(v)) return "[" + v.map(canonical).join(",") + "]";
    if (typeof v === "object") return "{" + Object.keys(v).sort().map((k) => JSON.stringify(k) + ":" + canonical(v[k])).join(",") + "}";
    return JSON.stringify(v);
  }
  const PASSAGE_RE = /\[(P\d+)\] ([\s\S]*?)(?=\n\n\[P\d+\] |\n\nQuestion: )/g;
  const VERSION_SUFFIX = "-(\\d{4}-\\d{2}-\\d{2}|\\d{8}|\\d{4}|latest)";
  const reEsc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  function modelMatch(requested, served) {
    const rq = (requested || "").trim().toLowerCase(), sv = (served || "").trim().toLowerCase();
    if (!rq || !sv) return [false, "mismatch"];
    if (rq === sv) return [true, "exact"];
    const sp = (s) => { const i = s.lastIndexOf("/"); return i < 0 ? ["", s] : [s.slice(0, i), s.slice(i + 1)]; };
    const [rv, rn] = sp(rq), [svv, sn] = sp(sv);
    if (rv && svv && rv !== svv) return [false, "mismatch"];
    if (rn === sn) return [true, "exact"];
    if (new RegExp("^" + reEsc(rn) + VERSION_SUFFIX + "$").test(sn)) return [true, "version"];
    return [false, "mismatch"];
  }
  function servedModel(respBytes) {
    try { const r = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(respBytes));
      if (!r || typeof r !== "object" || Array.isArray(r)) throw 0;
      return { served: r.model ?? null, provider: r.provider ?? null };
    } catch (e) { return { served: null, provider: null }; }
  }
  const py = (x) => (x === null || x === undefined ? "None" : String(x));
  const nn = (x) => (x === undefined ? null : x);
  // Python repr() for the JSON values that appear in check details (str, list, dict, None, bool, number)
  function pyRepr(v) {
    v = nn(v);
    if (v === null) return "None";
    if (v === true) return "True"; if (v === false) return "False";
    if (typeof v === "number") return Number.isInteger(v) ? String(v) : String(v);
    if (typeof v === "string") {
      const q = v.includes("'") && !v.includes('"') ? '"' : "'";
      let o = ""; for (const ch of v) { const c = ch.codePointAt(0);
        if (ch === "\\") o += "\\\\"; else if (ch === q) o += "\\" + q; else if (ch === "\n") o += "\\n"; else if (ch === "\r") o += "\\r"; else if (ch === "\t") o += "\\t";
        else if (c < 0x20 || c === 0x7f) o += "\\x" + c.toString(16).padStart(2, "0"); else o += ch; }
      return q + o + q;
    }
    if (Array.isArray(v)) return "[" + v.map(pyRepr).join(", ") + "]";
    return "{" + Object.keys(v).map((k) => pyRepr(k) + ": " + pyRepr(v[k])).join(", ") + "}";
  }
  const deq = (a, b) => JSON.stringify(nn(a)) === JSON.stringify(nn(b));
  function parseTs(s) {
    const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})\.(\d{1,6})Z$/.exec(s || "");
    if (!m) throw new Error(`time data '${s}' does not match format`);
    return Date.UTC(+m[1], m[2] - 1, +m[3], +m[4], +m[5], +m[6]) / 1000 + Number("0." + m[7]);
  }

  // ---------- cast / explorer helpers ----------
  function castFor(method, params, rpc) {
    const R = ` --rpc-url ${rpc}`;
    if (method === "eth_chainId") return `cast chain-id${R}`;
    if (method === "eth_getCode") return `cast code ${params[0]}${R}`;
    if (method === "eth_getTransactionByHash") return `cast tx ${params[0]}${R}`;
    if (method === "eth_getTransactionReceipt") return `cast receipt ${params[0]}${R}`;
    if (method === "eth_getBlockByNumber") return `cast block ${parseInt(params[0], 16)}${R}`;
    if (method === "eth_call") {
      const d = params[0].data.replace(/^0x/, ""), sig = SEL2SIG[d.slice(0, 8)], arg = d.slice(8);
      if (!sig) return `cast call ${params[0].to} ${params[0].data}${R}`;
      let a = "";
      if (arg) a = sig.includes("(address)") ? " 0x" + arg.slice(24) : sig.includes("(uint256)") ? " " + BigInt("0x" + arg).toString()
        : sig.includes("[]") ? ` "[${(arg.slice(128).match(/.{64}/g) || []).map((x) => "0x" + x).join(",")}]"` : " 0x" + arg;
      return `cast call ${params[0].to} "${sig}${SIGS[sig]}"${a}${R}`;
    }
    return `cast rpc ${method} '${JSON.stringify(params)}'${R}`;
  }
  function explorerFor(method, params) {
    if (method === "eth_getTransactionByHash") return `${EXPLORER}/tx/${params[0]}`;
    if (method === "eth_getTransactionReceipt") return `${EXPLORER}/tx/${params[0]}?tab=logs`;
    if (method === "eth_getBlockByNumber") return `${EXPLORER}/block/${parseInt(params[0], 16)}`;
    if (method === "eth_call") return `${EXPLORER}/address/${params[0].to}?tab=read_contract`;
    if (method === "eth_getCode") return `${EXPLORER}/address/${params[0]}?tab=contract`;
    return null;
  }

  // ---------- verifier ----------
  async function verifyBundle(b, opts = {}) {
    const rpcUrl = opts.rpc || RPC_URL, evidence = {}, calls = [];
    let tag = "net";
    const ev = (t, item) => (evidence[t] = evidence[t] || []).push(item);
    const cmp = (label, local, chain, extra) => ev(tag, { kind: "cmp", label, local, chain, ok: local === chain, ...extra });
    async function rpc(method, params) {
      const id = calls.length + 1, req = { jsonrpc: "2.0", id, method, params }, t0 = performance.now();
      let out;
      for (let attempt = 0; ; attempt++) { // the public RPC rate-limits bursts: back off and retry
        const r = await fetch(rpcUrl, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(req) });
        out = r.status === 429 ? { error: { code: -32005, message: "rate limit exceeded" } } : await r.json();
        if (!(out.error && out.error.code === -32005) || attempt >= 6) break;
        await new Promise((ok) => setTimeout(ok, 400 * 2 ** attempt));
      }
      const item = { kind: "rpc", req, res: out, ms: Math.round(performance.now() - t0), cast: castFor(method, params, rpcUrl), href: explorerFor(method, params) };
      calls.push(item); ev(tag, item); if (opts.onCall) opts.onCall(item);
      if (out.error) throw new Error(`${method}: ${JSON.stringify(out.error)}`);
      return out.result;
    }
    const call = async (to, sig, argHex = "") => fromHex(await rpc("eth_call", [{ to, data: "0x" + SEL[sig] + argHex }, "latest"]));

    // header
    const chain = parseInt(await rpc("eth_chainId", []), 16), contract = b.contract;
    const head = [{ check: "chain id is Arc testnet 5042002", ok: chain === CHAIN_ID && CHAIN_ID === parseInt(b.chain_id ?? CHAIN_ID), detail: `RPC says ${chain}` }];
    const code = await rpc("eth_getCode", [contract, "latest"]);
    head.push({ check: "SourceRegistry has contract code", ok: !["0x", "0x0"].includes(code), detail: contract });
    if (!head.every((x) => x.ok)) return { all_pass: false, header: head, citations: [], model_call: [], rpc_calls: calls.length, evidence, calls };

    // citations
    const cites = [];
    for (let i = 0; i < (b.citations || []).length; i++) { tag = "src:" + i; cites.push(await verifyCitation(b.citations[i])); }
    async function verifyCitation(c) {
      const fails = [], notes = [], text = c.text ?? "", claimed = (c.content_hash || "").toLowerCase();
      const h = await shaStr(text);
      cmp("sha256(text) vs bundle hash", h, claimed, { cmd: "jq -j '.citations[" + b.citations.indexOf(c) + "].text' bundle.json | sha256sum" });
      if (h !== claimed) fails.push(`text was altered: sha256(text)=${h.slice(0, 18)}... but bundle says ${claimed.slice(0, 18)}...`);
      const cid = cidV1Raw(h.slice(2));
      if (c.cid && c.cid !== cid) fails.push(`CID mismatch: recomputed ${cid.slice(0, 20)}... bundle ${c.cid.slice(0, 20)}...`);
      const src = await call(contract, "getSource(bytes32)", h.slice(2));
      const exists = asInt(word(src, 0)) === 1n;
      if (!exists) { fails.push("hash of this text is NOT registered on-chain"); return { id: c.id ?? null, status: "FAIL", reasons: fails, recomputed_hash: h }; }
      const onchainCid = dynString(src, Number(asInt(word(src, 1)))), registrant = asAddr(word(src, 3)), ts = Number(asInt(word(src, 4)));
      cmp("CID from text vs on-chain CID", cid, onchainCid);
      notes.push(`registered on-chain by ${registrant} at unix ${ts}; on-chain CID ${onchainCid}`);
      if (onchainCid && onchainCid !== cid) fails.push("on-chain CID differs from CID recomputed from the text");
      if (c.registrant && c.registrant.toLowerCase() !== registrant.toLowerCase()) fails.push(`bundle claims registrant ${c.registrant} but chain says ${registrant}`);
      const trusted = asInt(await call(contract, "isTrustedPublisher(address)", padAddr(registrant))) === 1n;
      const verified = asInt(await call(contract, "isVerified(bytes32)", h.slice(2))) === 1n;
      ev(tag, { kind: "fact", label: "publisher", value: registrant, ok: trusted, note: trusted ? "on the trusted-publisher allowlist" : "NOT trusted" });
      if (verified !== trusted) fails.push("isVerified disagrees with isTrustedPublisher(registrant)");
      if (!trusted) fails.push(`registered, but registrant ${registrant} is NOT a trusted publisher`);
      const tx = c.registration_tx;
      if (tx) {
        const t = await rpc("eth_getTransactionByHash", [tx]), rc = await rpc("eth_getTransactionReceipt", [tx]);
        if (t == null || rc == null) fails.push(`registration tx ${tx} not found on this chain`);
        else {
          const [fn, hs] = hashesInCalldata(t.input);
          if ((t.to || "").toLowerCase() !== contract.toLowerCase()) fails.push("registration tx was not sent to this contract");
          if (t.from.toLowerCase() !== registrant.toLowerCase()) fails.push("registration tx sender is not the on-chain registrant");
          if (parseInt(rc.status, 16) !== 1) fails.push("registration tx reverted");
          ev(tag, { kind: "fact", label: `hash in ${fn} calldata`, value: `${hs.length} hashes in tx input`, ok: hs.includes(h) });
          if (!hs.includes(h)) fails.push(`hash not found in the ${fn} calldata of the cited tx`);
          const topic = fn === "registerBatch" ? TOPIC.BATCH : TOPIC.SINGLE;
          if (!rc.logs.some((l) => l.address.toLowerCase() === contract.toLowerCase() && l.topics[0] === topic)) fails.push("cited tx emitted no registry event from this contract");
          const blk = await rpc("eth_getBlockByNumber", [rc.blockNumber, false]);
          cmp("block time of tx vs on-chain registration time", String(parseInt(blk.timestamp, 16)), String(ts));
          if (parseInt(blk.timestamp, 16) !== ts) fails.push("cited tx's block time != on-chain registration time (it did not register this hash)");
          if (c.block_number != null && parseInt(rc.blockNumber, 16) !== parseInt(c.block_number)) fails.push("bundle block_number does not match the tx's block");
          if (!fails.length) notes.push(`tx ${tx.slice(0, 12)}... ${fn} of ${hs.length} hashes in block ${parseInt(rc.blockNumber, 16)} includes it`);
        }
      } else notes.push("no registration_tx given: skipped tx/event check");
      return { id: c.id ?? null, status: fails.length ? "FAIL" : "PASS", reasons: fails, notes, recomputed_hash: h };
    }

    // model call
    const inf = b.inference ? await verifyInference() : [];
    async function verifyInference() {
      const I = b.inference, out = [], log = I.log_contract, aid = I.answer_id.toLowerCase();
      const chk = (check, ok, detail = "", t) => { out.push({ check, ok: !!ok, detail, tag: t || tag }); return !!ok; };
      tag = "record";
      if (!chk("M1 log contract has code", !["0x", "0x0"].includes(await rpc("eth_getCode", [log, "latest"])), log)) return out;
      const rec = await call(log, "getRecord(bytes32)", aid.slice(2));
      const K = ["questionId", "requestHash", "passagesRoot", "modelHash", "responseHash", "logger", "passageCount", "commitBlock", "commitTime", "finalizeBlock", "finalizeTime"];
      const f = {}; K.forEach((k, i) => (f[k] = word(rec, i)));
      const hexw = (k) => "0x" + toHex(f[k]);
      const [cb, ct, fb, ft] = ["commitBlock", "commitTime", "finalizeBlock", "finalizeTime"].map((k) => Number(asInt(f[k])));
      if (!chk("M1 record exists for answer_id", cb !== 0, aid)) return out;
      const logger = asAddr(f.logger);
      const isLog = asInt(await call(log, "isLogger(address)", padAddr(logger))) === 1n;
      chk("M1 written by an authorized logger", isLog, `logger ${logger}; log owner ${asAddr(await call(log, "owner()"))}`);
      chk("M1 finalized", fb !== 0);

      tag = "prompt";
      const rh = await shaStr(I.request_body);
      cmp("sha256(request bytes) vs on-chain requestHash", rh, hexw("requestHash"), { cmd: "jq -j .inference.request_body bundle.json | sha256sum" });
      chk("M2 sha256(request bytes) == on-chain requestHash", rh === hexw("requestHash"), `recomputed ${rh.slice(0, 18)}... on-chain ${hexw("requestHash").slice(0, 18)}...`);
      let body; try { body = JSON.parse(I.request_body); } catch (e) { chk("M2 request body is JSON", false, String(e)); return out; }
      chk("M2 request bytes are canonical JSON", canonical(body) === I.request_body);
      const msgs = body.messages || [], sysmsg = (msgs.find((m) => m.role === "system") || {}).content || "", user = (msgs.find((m) => m.role === "user") || {}).content || "";
      tag = "model";
      const mh = await shaStr(I.model);
      cmp("sha256(requested model) vs on-chain modelHash", mh, hexw("modelHash"), { cmd: `printf %s '${I.model}' | sha256sum` });
      chk("M2 model in request == bundle model, sha256(model) == on-chain modelHash", body.model === I.model && mh === hexw("modelHash"), body.model);
      tag = "prompt";
      const qh = await shaStr(I.question_key);
      chk("M2 question_id == sha256(question_key) == on-chain questionId", qh === I.question_id.toLowerCase() && I.question_id.toLowerCase() === hexw("questionId"), I.question_key);
      chk("M2 answer_id, question_id, client_ts are inside the hashed request",
        sysmsg.includes(`answer_id=${I.answer_id}`) && sysmsg.includes(`question_id=${I.question_id}`) && sysmsg.includes(`client_ts=${I.client_request_ts}`));
      chk("M2 question text in request == bundle question", user.trimEnd().endsWith("Question: " + (b.question ?? "")));
      const segs = [...user.matchAll(PASSAGE_RE)].map((m) => [m[1], m[2]]);
      const promptHashes = []; for (const [pid, t] of segs) promptHashes.push([pid, await shaStr(t)]);
      const listed = (I.passages || []).map((p) => [p.id, p.content_hash.toLowerCase()]);
      chk("M3 passages parsed from committed prompt == bundle passage list", JSON.stringify(promptHashes) === JSON.stringify(listed), `${promptHashes.length} in prompt, ${listed.length} listed`);
      const root = await sha256(concat(promptHashes.map(([, h]) => fromHex(h))));
      cmp("passages root from prompt vs on-chain passagesRoot", root, hexw("passagesRoot"));
      chk("M3 passagesRoot over prompt passages == on-chain passagesRoot and count matches", root === hexw("passagesRoot") && promptHashes.length === Number(asInt(f.passageCount)), `${promptHashes.length} passages`);
      for (let i = 0; i < (b.citations || []).length; i++) {
        const c = b.citations[i], th = await shaStr(c.text ?? "");
        const inprompt = user.includes(`[${c.id}] ${c.text ?? ""}`), inlist = promptHashes.some(([p, h]) => p === c.id && h === th);
        chk(`M3 cited ${c.id} text is verbatim in the committed prompt under that id`, inprompt && inlist, inprompt && inlist ? "" : "this passage was NOT in the prompt sent to the model", "src:" + i);
      }

      tag = "answer";
      const resp = b64bytes(I.response_body_b64), rsh = await sha256(resp), respOk = rsh === hexw("responseHash");
      cmp("sha256(raw response bytes) vs on-chain responseHash", rsh, hexw("responseHash"), { cmd: "jq -r .inference.response_body_b64 bundle.json | base64 -d | sha256sum" });
      chk("M4 sha256(raw response bytes) == on-chain responseHash", respOk, `recomputed ${rsh.slice(0, 18)}... on-chain ${hexw("responseHash").slice(0, 18)}...`);
      const sm = servedModel(resp), [match, kind] = modelMatch(I.model, sm.served || "");
      tag = "model";
      ev("model", { kind: "fact", label: "served model (inside hashed response)", value: `${py(sm.served)} · provider ${py(sm.provider)}`, ok: match && respOk, note: `requested ${I.model} · ${kind} · self-reported by the provider` });
      chk("M6 served model (from the hashed response) matches the requested model", respOk && match,
        `requested ${I.model} -> served ${py(sm.served)} (${kind}${respOk ? "" : ", response bytes not authentic"}) · provider ${py(sm.provider)} (self-reported)`);
      tag = "answer";
      let mj;
      try { const content = JSON.parse(new TextDecoder().decode(resp)).choices[0].message.content;
        const m = /\{[\s\S]*\}/.exec(content); if (!m) throw new Error("no JSON object in content"); mj = JSON.parse(m[0]);
      } catch (e) { chk("M4 response parses", false, String(e)); return out; }
      chk("M4 bundle answer == answer in the logged response", deq(mj.answer, b.answer), `response says ${pyRepr(mj.answer)}, bundle says ${pyRepr(b.answer)}`);
      const bc = (b.citations || []).map((c) => c.id), mc = Array.isArray(mj.citations) ? mj.citations : [];
      chk("M4 bundle citation ids == citation ids in the logged response", JSON.stringify(bc) === JSON.stringify(mc), `response cites ${pyRepr(mj.citations)}, bundle cites ${pyRepr(bc)}`);

      chk("M5 commit block < finalize block", 0 < cb && cb < fb, `commit block ${cb} (${ct}), finalize block ${fb} (${ft})`);
      for (const [name, txh, sel, topic, blk, tg] of [["commit", I.commit_tx, SEL["commit(bytes32,bytes32,bytes32,bytes32,bytes32,uint16)"], TOPIC.COMMITTED, cb, "prompt"],
                                                       ["finalize", I.finalize_tx, SEL["finalize(bytes32,bytes32)"], TOPIC.FINALIZED, fb, "answer"]]) {
        tag = tg;
        const t = await rpc("eth_getTransactionByHash", [txh]), r = await rpc("eth_getTransactionReceipt", [txh]);
        const good = !!(t && r) && (t.to || "").toLowerCase() === log.toLowerCase() && t.input.slice(2, 10) === sel && t.input.slice(10, 74) === aid.slice(2)
          && parseInt(r.status, 16) === 1 && parseInt(r.blockNumber, 16) === blk
          && r.logs.some((l) => l.address.toLowerCase() === log.toLowerCase() && l.topics[0] === topic && l.topics[1] === aid);
        chk(`M5 ${name} tx is the real log tx for this answer_id`, good, txh);
      }
      tag = "prompt";
      try { const cts = parseTs(I.client_request_ts);
        chk("M5 client request timestamp is not after the commit (<=60 s skew)", cts <= ct + 60, `client ${cts.toFixed(1)} vs commit block time ${ct}`);
      } catch (e) { chk("M5 client timestamp parses", false, String(e.message || e)); }
      return out;
    }

    const ok = cites.length > 0 && cites.every((r) => r.status === "PASS") && inf.every((x) => x.ok);
    let model = null;
    if (b.inference) {
      let sm; try { sm = servedModel(b64bytes(b.inference.response_body_b64)); } catch (e) { sm = { served: null, provider: null }; }
      const [m, kind] = modelMatch(b.inference.model || "", sm.served || "");
      model = { requested: b.inference.model ?? null, served: sm.served, provider: sm.provider, match: m, kind };
    }
    // corpus / retrieval (C checks)
    const cor = b.retrieval ? await verifyCorpus() : [];
    async function verifyCorpus() {
      const R = b.retrieval, out = [], reg = R.corpus_registry, ver = Number(R.corpus_version);
      const chk = (check, ok, detail = "") => { out.push({ check, ok: ok === null ? null : !!ok, detail, tag: "corpus" }); return !!ok; };
      tag = "corpus"; const u256 = ver.toString(16).padStart(64, "0");
      if (!chk("C1 CorpusRegistry has code", !["0x", "0x0"].includes(await rpc("eth_getCode", [reg, "latest"])), reg)) return out;
      const info = await call(reg, "corpusInfo(uint256)", u256);
      const root = "0x" + toHex(word(info, 0)), cfgHash = "0x" + toHex(word(info, 1)), leaves = Number(asInt(word(info, 2))), publisher = asAddr(word(info, 3));
      cmp("corpus root on-chain vs bundle", R.corpus_root.toLowerCase(), root);
      chk("C1 on-chain corpus version matches the bundle (root, leaf count, config hash)",
        root === R.corpus_root.toLowerCase() && leaves === Number(R.leaf_count) && cfgHash === R.config_hash.toLowerCase(), `v${ver} · ${leaves} leaves`);
      chk("C1 corpus published by an authorized publisher", asInt(await call(reg, "isPublisher(address)", padAddr(publisher))) === 1n, publisher);
      const rc = await rpc("eth_getTransactionReceipt", [R.publish_tx]);
      chk("C1 publish tx emitted CorpusPublished(version, root)", !!rc && parseInt(rc.status, 16) === 1 && rc.logs.some((l) =>
        l.address.toLowerCase() === reg.toLowerCase() && l.topics[0] === TOPIC.PUBLISHED && BigInt(l.topics[1]) === BigInt(ver) && l.topics[2] === root), R.publish_tx);
      const ch = await shaStr(R.config_json);
      cmp("sha256(retrieval config) vs on-chain configHash", ch, cfgHash, { cmd: "jq -j .retrieval.config_json bundle.json | sha256sum" });
      chk("C2 sha256(retrieval config) == on-chain configHash", ch === cfgHash, `recomputed ${ch.slice(0, 18)}...`);
      const cfg = JSON.parse(R.config_json), ranked = JSON.parse(R.ranked_json), rh = await shaStr(R.ranked_json);
      let body = {}; try { body = b.inference ? JSON.parse(b.inference.request_body) : {}; } catch (e) {}
      const sysmsg = ((body.messages || []).find((m) => m.role === "system") || {}).content || "";
      const inReq = (/retrieval=(0x[0-9a-f]{64})/.exec(sysmsg) || [])[1] || "(absent)";
      cmp("sha256(ranked list) vs hash inside the hashed request", rh, inReq, { cmd: "jq -j .retrieval.ranked_json bundle.json | sha256sum" });
      chk("C2 corpus, config and ranked-list hashes are inside the hashed request",
        sysmsg.includes(`corpus=v${ver}:${root}`) && sysmsg.includes(`retrieval_config=${cfgHash}`) && sysmsg.includes(`retrieval=${rh}`), `sha256(ranked list) ${rh.slice(0, 18)}...`);
      const proofs = R.proofs || [], RP = G.ArcReplay;
      const pv = ranked.map((x, r) => r < proofs.length ? RP.merkleVerify(x.id, Number(proofs[r].leaf_index), leaves, proofs[r].path, root) : { ok: false, steps: [] });
      const bad = pv.findIndex((x) => !x.ok);
      chk("C3 every ranked candidate has a valid inclusion proof against the on-chain root", bad < 0,
        bad < 0 ? `${ranked.length} of ${ranked.length} proofs valid` : `candidate #${bad + 1} ${ranked[bad].id.slice(0, 12)}... is not in the committed corpus`);
      const ids = ranked.map((x) => x.id), prompt = ((b.inference || {}).passages || []).map((p) => p.content_hash.toLowerCase());
      // show one proof path: the first cited passage (else the first failing / first candidate)
      const c0 = (b.citations || [])[0], show = bad >= 0 ? bad : Math.max(0, c0 ? ids.indexOf(c0.content_hash.toLowerCase()) : 0);
      if (ranked.length) ev("corpus", { kind: "proof", label: (c0 && bad < 0 ? `inclusion proof for ${c0.id}` : `inclusion proof for candidate #${show + 1}`),
        id: ids[show], index: proofs[show] ? proofs[show].leaf_index : null, leaves, root, ...pv[show] });
      chk("C3 every prompt passage is a ranked candidate", prompt.every((h) => ids.includes(h)), `${prompt.length} prompt passages`);
      const vb = await call(contract, "areVerified(bytes32[])", (32).toString(16).padStart(64, "0") + ids.length.toString(16).padStart(64, "0") + ids.map((h) => h.slice(2).toLowerCase()).join(""));
      const off = Number(asInt(word(vb, 0))), n = Number(asInt(vb.slice(off, off + 32))), vnow = [];
      for (let i = 0; i < n; i++) vnow.push(asInt(vb.slice(off + 32 + 32 * i, off + 64 + 32 * i)) === 1n);
      const want = ids.filter((h, i) => vnow[i]).slice(0, Number(cfg.selection.prompt_k));
      chk("C4 prompt = the first k verified candidates in rank order (none skipped)", JSON.stringify(want) === JSON.stringify(prompt),
        `${vnow.filter(Boolean).length} of ${ids.length} candidates verified on SourceRegistry`);
      if (opts.replay === false) { chk("C5 replay of the committed retrieval", null, "skipped (--no-replay)"); return out; }
      const ub = await call(reg, "corpusUri(uint256)", u256), uri = dynString(ub, Number(asInt(word(ub, 0))));
      let arts;
      try { arts = await loadArtifacts(cfg, uri, opts); } catch (e) { chk("C5 replay of the committed retrieval", null, `not run: ${e.name || "Error"}`); ev("corpus", { kind: "replay", error: String(e.message || e), uri }); return out; }
      const names = arts.filter((a) => !a.ok).map((a) => a.name);
      if (!chk("C5 fetched corpus, retriever code, weights and tokenizer match the committed config", !names.length,
        !names.length ? "sha256 match" : `${names[0]} sha256 differs from the committed config`)) { ev("corpus", { kind: "replay", arts, uri }); return out; }
      const A = Object.fromEntries(arts.map((a) => [a.name, a.data])), t0 = performance.now();
      const key = cfgHash; let ix = IDX.get(key), rows;
      if (!ix) { rows = new TextDecoder().decode(A.corpus).split("\n").filter((l) => l.trim()).map((l) => JSON.parse(l)); }
      else rows = ix.docs;
      const froot = RP.merkleRoot(rows.map((r) => r.hash));
      chk("C5 Merkle root of the fetched corpus == on-chain root", froot === root && rows.length === leaves, `${rows.length} passages`);
      if (!ix) { const wb = A["model weights"]; ix = RP.buildIndex(cfg, rows, JSON.parse(new TextDecoder().decode(A.tokenizer)), wb.buffer.slice(wb.byteOffset, wb.byteOffset + wb.byteLength)); IDX.set(key, ix); }
      const q = R.query ?? b.question ?? "", rep = RP.rank(ix, q), d = replayDiff(ranked, rep);
      chk("C5 replaying the committed retriever gives the same ranked candidates", !d, !d ? `${rep.length} candidates, identical ids and order` : d);
      chk("C5 replay query == bundle question", (R.query ?? b.question) === b.question);
      ev("corpus", { kind: "replay", arts: arts.map(({ data, ...a }) => a), uri, froot, ms: Math.round(performance.now() - t0), ok: !d, diff: d,
        rows: rep.map((x, i) => ({ rank: i + 1, id: x.id, replay: x, committed: ranked[i] || null, same: !!ranked[i] && ranked[i].id === x.id })),
        missing: rep.filter((x) => !ids.includes(x.id)).map((x) => x.id) });
      return out;
    }

    tag = "net";
    const okAll = ok && cor.every((x) => x.ok !== false);
    const owner = asAddr(await call(contract, "owner()"));
    return { all_pass: okAll, model, header: head, citations: cites, model_call: inf, corpus: cor, owner, rpc_calls: calls.length, rpc: rpcUrl, evidence, calls };
  }

  // ---------- committed artifacts (fetched from the on-chain URI, sha256-checked; cached per page) ----------
  const ART = new Map(), IDX = new Map();
  async function loadArtifacts(cfg, configUrl, opts) {
    const list = [["corpus", cfg.corpus], ["retriever code", cfg.retriever_code], ["model weights", cfg.model.weights], ["tokenizer", cfg.model.tokenizer]];
    return Promise.all(list.map(async ([name, a]) => {
      const url = new URL(a.path, configUrl).href;
      let data = ART.get(a.sha256);
      if (!data) {
        if (opts.onStatus) opts.onStatus(`fetching ${name}`);
        data = opts.loadArtifact ? await opts.loadArtifact(a.path) : new Uint8Array(await (await fetch(url, { cache: "force-cache" })).arrayBuffer());
      }
      const h = await sha256(data), ok = h === a.sha256;
      if (ok) ART.set(a.sha256, data);
      return { name, url, sha256: h, expected: a.sha256, ok, bytes: data.length, data };
    }));
  }
  function replayDiff(committed, replayed) {
    const cids = committed.map((x) => x.id);
    for (let r = 0; r < replayed.length; r++) if (!cids.includes(replayed[r].id)) return `replay ranks ${replayed[r].id.slice(0, 12)}... at #${r + 1}, but the answer's list omits it`;
    for (let r = 0; r < Math.min(replayed.length, committed.length); r++) { const a = replayed[r], c = committed[r];
      if (a.id !== c.id) return `rank #${r + 1} differs: replay ${a.id.slice(0, 12)}..., answer ${c.id.slice(0, 12)}...`;
      if (nn(a.bm25) !== nn(c.bm25) || nn(a.dense) !== nn(c.dense) || Math.abs(a.rrf - Number(c.rrf || 0)) > 1e-9) return `rank #${r + 1} scores differ`; }
    if (replayed.length !== committed.length) return `replay returns ${replayed.length} candidates, the answer lists ${committed.length}`;
    return "";
  }

  // demo tampering (client-side, on a copy)
  function tamper(b, mode) {
    const t = JSON.parse(JSON.stringify(b));
    if (mode === "char" && t.citations.length) { const c = t.citations[0], i = Math.floor(c.text.length / 2);
      c.text = c.text.slice(0, i) + (c.text[i] === "x" ? "y" : "x") + c.text.slice(i + 1); t._t = `one character of ${c.id}`; }
    else if (mode === "answer") { t.answer = t.answer + " (edited)"; t._t = "the answer"; }
    else if (mode === "model") { const raw = new TextDecoder().decode(b64bytes(t.inference.response_body_b64))
        .replace(/"model":"[^"]*"/, '"model":"meta-llama/llama-3.1-8b-instruct"');
      let bin = ""; enc.encode(raw).forEach((x) => (bin += String.fromCharCode(x))); t.inference.response_body_b64 = btoa(bin); t._t = "served model"; }
    else if (mode === "hide" && t.retrieval) { const r = JSON.parse(t.retrieval.ranked_json), i = 0;
      t._t = `ranking (hid #${i + 1})`; r.splice(i, 1); t.retrieval.proofs.splice(i, 1); t.retrieval.ranked_json = canonical(r); }
    else { t.inference.request_body = t.inference.request_body.replace("[P1] ", "[P1] Note: "); t._t = "the prompt"; }
    return t;
  }

  G.ArcVerify = { tamper, verifyBundle, cidV1Raw, canonical, modelMatch, servedModel, SEL, TOPIC, RPC_URL, EXPLORER, CHAIN_ID };
})(globalThis);
