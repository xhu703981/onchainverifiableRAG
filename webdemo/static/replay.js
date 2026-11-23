/* ArcReplay: independent JS implementation of the committed retriever (retrieval_v1.py, "hybrid-v1")
   plus the corpus Merkle tree. Used to replay retrieval in the browser: given the committed corpus,
   model weights and config (all hash-checked), it recomputes the ranked candidates for a question.
   Deterministic: float64 throughout, orderings on scores quantised to 1e-9, ties by leaf index. */
(function (G) {
  "use strict";
  const N = () => G.nobleHashes;
  const toHex = (u8) => Array.from(u8, (x) => x.toString(16).padStart(2, "0")).join("");
  const fromHex = (h) => { h = h.replace(/^0x/, ""); const o = new Uint8Array(h.length / 2); for (let i = 0; i < o.length; i++) o[i] = parseInt(h.substr(2 * i, 2), 16); return o; };

  // ---------- Merkle: leaf = sha256(0x00||passageHash), node = sha256(0x01||l||r), odd node promoted ----------
  const H = (tag, ...parts) => { const n = 1 + parts.reduce((s, p) => s + p.length, 0), b = new Uint8Array(n); b[0] = tag; let o = 1; for (const p of parts) { b.set(p, o); o += p.length; } return N().sha256(b); };
  const leaf = (hashHex) => H(0, fromHex(hashHex));
  function merkleRoot(hashesHex) {
    let cur = hashesHex.map(leaf);
    while (cur.length > 1) { const nx = []; for (let i = 0; i < cur.length; i += 2) nx.push(i + 1 < cur.length ? H(1, cur[i], cur[i + 1]) : cur[i]); cur = nx; }
    return "0x" + toHex(cur[0]);
  }
  // returns the list of intermediate steps so the UI can show the path; .ok tells if it reaches `root`
  function merkleVerify(hashHex, index, leafCount, path, root) {
    const steps = []; if (!(index >= 0 && index < leafCount)) return { ok: false, steps };
    let h = leaf(hashHex), n = leafCount, p = 0, i = index;
    steps.push({ op: "leaf", value: "0x" + toHex(h) });
    try {
      while (n > 1) {
        if (i % 2 === 1) { h = H(1, fromHex(path[p]), h); steps.push({ op: "L", sib: path[p], value: "0x" + toHex(h) }); p++; }
        else if (i + 1 < n) { h = H(1, h, fromHex(path[p])); steps.push({ op: "R", sib: path[p], value: "0x" + toHex(h) }); p++; }
        else steps.push({ op: "up", value: "0x" + toHex(h) });
        i = Math.floor(i / 2); n = Math.ceil(n / 2);
      }
    } catch (e) { return { ok: false, steps }; }
    if (path.some((x) => typeof x !== "string" || !/^0x[0-9a-f]{64}$/i.test(x))) return { ok: false, steps };
    return { ok: p === path.length && "0x" + toHex(h) === String(root).toLowerCase(), steps };
  }

  // ---------- tokenizers ----------
  const RE_C = /\p{C}/u, RE_MN = /\p{Mn}/u, RE_P = /\p{P}/u, RE_WS = /\s/u;
  const cjk = (cp) => (cp >= 0x4E00 && cp <= 0x9FFF) || (cp >= 0x3400 && cp <= 0x4DBF) || (cp >= 0x20000 && cp <= 0x2A6DF) || (cp >= 0x2A700 && cp <= 0x2B73F)
    || (cp >= 0x2B740 && cp <= 0x2B81F) || (cp >= 0x2B820 && cp <= 0x2CEAF) || (cp >= 0xF900 && cp <= 0xFAFF) || (cp >= 0x2F800 && cp <= 0x2FA1F);
  const bm25Tokens = (s, stop) => (s.toLowerCase().match(/[a-z0-9]+/g) || []).filter((w) => !stop.has(w));
  function bertWords(text) {
    let o = "";
    for (const ch of text) { const cp = ch.codePointAt(0);
      if (cp === 0 || cp === 0xFFFD || (!"\t\n\r".includes(ch) && RE_C.test(ch))) continue;
      if (RE_WS.test(ch)) o += " "; else if (cjk(cp)) o += " " + ch + " "; else o += ch; }
    let s = ""; for (const ch of o.normalize("NFD")) if (!RE_MN.test(ch)) s += ch;
    s = s.toLowerCase();
    const words = []; let cur = "";
    for (const ch of s) { const cp = ch.codePointAt(0);
      if (RE_WS.test(ch)) { if (cur) { words.push(cur); cur = ""; } }
      else if ((cp >= 33 && cp <= 126 && !/[A-Za-z0-9]/.test(ch)) || RE_P.test(ch)) { if (cur) { words.push(cur); cur = ""; } words.push(ch); }
      else cur += ch; }
    if (cur) words.push(cur);
    return words;
  }
  function wordpieceIds(text, vocab, maxTokens) {
    const unk = vocab.get("[UNK]"), ids = [];
    for (const w of bertWords(text)) {
      const cs = Array.from(w);
      if (cs.length > 100) { ids.push(unk); continue; }
      let start = 0, pieces = [];
      while (start < cs.length) {
        let end = cs.length, cur;
        while (start < end) { const sub = (start ? "##" : "") + cs.slice(start, end).join(""); if (vocab.has(sub)) { cur = vocab.get(sub); break; } end--; }
        if (cur === undefined) { pieces = [unk]; break; }
        pieces.push(cur); start = end;
      }
      ids.push(...pieces);
    }
    return ids.slice(0, maxTokens).filter((i) => i !== unk);
  }

  // ---------- model ----------
  function parseSafetensors(buf) {
    const dv = new DataView(buf), n = dv.getUint32(0, true) + dv.getUint32(4, true) * 2 ** 32;
    const meta = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 8, n))).embeddings;
    if (meta.dtype !== "F32") throw new Error("weights are not F32");
    const [a, b] = meta.data_offsets, data = new Float32Array(buf.slice(8 + n + a, 8 + n + b));
    return { W: data, rows: meta.shape[0], dim: meta.shape[1] };
  }

  // ---------- retriever ----------
  const q9 = (x) => Math.round(x * 1e9);
  const top = (sc, n) => sc.map((_, i) => i).sort((i, j) => q9(sc[j]) - q9(sc[i]) || i - j).slice(0, n);
  function buildIndex(cfg, docs, tokenizerJson, weightsBuf) {
    const vocab = new Map(Object.entries(tokenizerJson.model.vocab)), M = parseSafetensors(weightsBuf);
    const stop = new Set(cfg.bm25.stopwords), maxT = cfg.dense.max_tokens;
    const embed = (text) => { const acc = new Float64Array(M.dim);
      for (const id of wordpieceIds(text, vocab, maxT)) { const o = id * M.dim; for (let j = 0; j < M.dim; j++) acc[j] += M.W[o + j]; }
      let s = 0; for (let j = 0; j < M.dim; j++) s += acc[j] * acc[j]; const nrm = Math.sqrt(s);
      if (nrm > 0) for (let j = 0; j < M.dim; j++) acc[j] = acc[j] / nrm; return acc; };
    const tf = [], df = new Map(), dl = [];
    for (const d of docs) { const t = bm25Tokens(d.text, stop), m = new Map(); for (const w of t) m.set(w, (m.get(w) || 0) + 1);
      for (const w of m.keys()) df.set(w, (df.get(w) || 0) + 1); tf.push(m); dl.push(t.length); }
    const Nd = docs.length, avgdl = dl.reduce((a, b) => a + b, 0) / Nd;
    return { cfg, docs, stop, embed, tf, df, dl, N: Nd, avgdl, E: docs.map((d) => embed(d.text)) };
  }
  function rank(ix, query) {
    const { k1, b } = ix.cfg.bm25, fu = ix.cfg.fusion, q = bm25Tokens(query, ix.stop), idf = new Map();
    for (const w of new Set(q)) if (ix.df.has(w)) { const n = ix.df.get(w); idf.set(w, Math.log(1 + (ix.N - n + 0.5) / (n + 0.5))); }
    const bm = new Array(ix.N);
    for (let i = 0; i < ix.N; i++) { let s = 0; const tf = ix.tf[i], norm = k1 * (1 - b + b * ix.dl[i] / ix.avgdl);
      for (const w of q) { const f = tf.get(w) || 0; if (f) s += idf.get(w) * f * (k1 + 1) / (f + norm); } bm[i] = s; }
    const qv = ix.embed(query), dn = ix.E.map((e) => { let s = 0; for (let j = 0; j < e.length; j++) s += e[j] * qv[j]; return s; });
    const B = top(bm, fu.list_depth), D = top(dn, fu.list_depth), rb = new Map(), rd = new Map(), sc = new Map();
    B.forEach((d, r) => rb.set(d, r + 1)); D.forEach((d, r) => rd.set(d, r + 1));
    for (const d of B) sc.set(d, 1 / (fu.k + rb.get(d)));
    for (const d of D) sc.set(d, (sc.get(d) || 0) + 1 / (fu.k + rd.get(d)));
    const order = [...sc.keys()].sort((x, y) => sc.get(y) - sc.get(x) || x - y).slice(0, fu.pool);
    return order.map((d) => ({ id: ix.docs[d].hash, rrf: sc.get(d), bm25: rb.has(d) ? rb.get(d) : null, dense: rd.has(d) ? rd.get(d) : null }));
  }

  G.ArcReplay = { merkleRoot, merkleVerify, bertWords, wordpieceIds, buildIndex, rank, parseSafetensors };
})(globalThis);
