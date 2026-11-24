#!/usr/bin/env python3
"""Verify a NEWS answer bundle (kind "news") against the public Arc testnet. Standard library only.
Normally run through verify_citations.py, which dispatches here for news bundles.

What it proves: each cited paragraph is byte-exact text from an article whose metadata (URL, outlet,
title, claimed times, raw-HTML hash) sits in a batch Merkle root that YOUR trusted registrant put on
Arc, and the model saw exactly that text (M-checks). It does NOT prove the news is true, and it cannot
detect articles that were left out of retrieval (full text is not redistributed, so retrieval can't be
replayed); the retrieval scope (set of batch roots + config hash) is fixed in the committed request.

Merkle scheme dispatch: bundle news.scheme == "news-merkle-v1" (the CorpusRegistry scheme is separate).
  paragraph leaf = sha256(0x00||utf8(text)); node = sha256(0x01||L||R), odd node promoted; 1 leaf: root = leaf
  article leaf   = sha256(0x02||sha256(JCS(meta))||article_root); batch root = Merkle(article leaves)
Checks:
  N1 paragraph text -> leaf -> proof -> article_root
  N2 meta (RFC 8785 JCS) + article_root -> article leaf -> proof -> batch_root (URL/outlet/times are bound)
  N3 batch_root registered on NewsRegistry; manifest hash matches; trusted: isTrustedRoot (the CURRENT allowlist),
     or with --trust, registrant in YOUR list
  N4 the registration tx is a successful registerBatch(root, ...) to the registry that emitted BatchRegistered,
     in a block whose time equals the on-chain registration time
  N5 claimed fetched_at (crawler, L0) is not after the batch block time
  N6 the manifest at the on-chain URI hashes to the on-chain manifestHash, lists this article, rebuilds the root
  S1-S4 retrieval scope: scope hash over the bundle's batch roots, every root currently trusted, as_of = max
     on-chain registration time over the scope, config hash, and all of these inside the committed request
  U1 (optional --outlets) every cited outlet is one of the sources YOU chose to trust
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.request

import verify_citations as vc

SCHEME = "news-merkle-v1"
META_KEYS = ("extractor_version", "fetched_at", "normalization", "outlet", "published_at", "raw_html_sha256", "title", "url", "version")
vc.SEL.update({  # `cast sig "..."`
    "getBatch(bytes32)": "ed42136f",
    "isTrustedRoot(bytes32)": "3cb692b8",
    "registerBatch(bytes32,bytes32,uint32,string)": "a5578159",
})
TOPIC_NEWS_BATCH = "0x381fea40433275a331b047dcd8053f7b4916bce5e7bf4653754bcfe15c6e3bd2"  # BatchRegistered(bytes32,bytes32,address,uint32,uint256)


def _sha(b: bytes) -> bytes:
    return hashlib.sha256(b).digest()


def _hx(b: bytes) -> str:
    return "0x" + b.hex()


def _unhx(s: str) -> bytes:
    return bytes.fromhex(s[2:] if s.startswith("0x") else s)


def jcs(v) -> bytes:
    """RFC 8785 for str/int/bool/null/list/dict (keys sorted by UTF-16 code units, ECMAScript string escaping)."""
    def enc(x):
        if x is None or isinstance(x, bool):
            return json.dumps(x)
        if isinstance(x, int):
            return str(x)
        if isinstance(x, str):
            return json.dumps(x, ensure_ascii=False)
        if isinstance(x, list):
            return "[" + ",".join(enc(y) for y in x) + "]"
        if isinstance(x, dict):
            return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + enc(x[k]) for k in sorted(x, key=lambda k: k.encode("utf-16-be"))) + "}"
        raise TypeError(type(x).__name__)
    return enc(v).encode("utf-8")


def node(l: bytes, r: bytes) -> bytes:
    return _sha(b"\x01" + l + r)


def apply_proof(leaf: bytes, path: list[str]) -> bytes:
    h = leaf
    for step in path:
        side, s = step.split(":", 1)
        sib = _unhx(s)
        if side not in ("L", "R") or len(sib) != 32:
            raise ValueError("bad proof step")
        h = node(sib, h) if side == "L" else node(h, sib)
    return h


def merkle_root(leaves: list[bytes]) -> bytes:
    lv = list(leaves)
    while len(lv) > 1:
        nxt = [node(lv[i], lv[i + 1]) for i in range(0, len(lv) - 1, 2)]
        if len(lv) % 2:
            nxt.append(lv[-1])
        lv = nxt
    return lv[0]


def article_leaf(meta: dict, article_root: bytes) -> bytes:
    return _sha(b"\x02" + _sha(jcs(meta)) + article_root)


def scope_hash(roots: list[str]) -> str:
    return _hx(_sha(b"".join(_unhx(r) for r in sorted({r.lower() for r in roots}))))


def _ts(s: str) -> int:
    return int(time.mktime(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone) if s else 0


def get_batch(rpc, registry: str, root: str) -> dict:
    b = rpc.call(registry, "getBatch(bytes32)", root.lower().removeprefix("0x"))
    w = lambda i: vc.word(b, i)
    return dict(exists=vc.as_int(w(0)) == 1, manifest_hash=_hx(w(1)), registrant=vc.as_addr(w(2)), article_count=vc.as_int(w(3)),
                timestamp=vc.as_int(w(4)), uri=vc.dyn_string(b, vc.as_int(w(5))))


def is_trusted_root(rpc, registry: str, root: str) -> bool:
    return vc.as_int(rpc.call(registry, "isTrustedRoot(bytes32)", root.lower().removeprefix("0x"))) == 1


def _fetch(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "verify-news/1.0"}), timeout=30) as r:
        return r.read()


def verify_news(rpc, b: dict, trust: set[str] | None = None, outlets: set[str] | None = None, manifests: bool = True) -> dict:
    """Returns {"header": [...], "citations": [{id, meta, checks: [(name, ok, detail)]}], "scope": [...]}. ok None = skipped."""
    news = b["news"]
    reg = news["registry"]
    head, scope_checks, cites = [], [], []
    head.append(("NewsRegistry has contract code", rpc("eth_getCode", [reg, "latest"]) not in ("0x", "0x0"), reg))
    head.append(("Merkle scheme is news-merkle-v1 (dispatch by version)", news.get("scheme") == SCHEME, news.get("scheme")))
    if not all(h[1] for h in head):
        return dict(header=head, citations=[], scope=[])
    batch_cache: dict[str, dict] = {}
    manifest_cache: dict[str, bytes | Exception] = {}

    def batch(root):
        if root not in batch_cache:
            batch_cache[root] = get_batch(rpc, reg, root)
        return batch_cache[root]

    for c in b.get("citations", []):
        out = []
        chk = lambda n, ok, d="": out.append((n, None if ok is None else bool(ok), d))
        text = c.get("text", "")
        p, a, bt = c["paragraph"], c["article"], c["batch"]
        meta = a["meta"]
        leaf = _sha(b"\x00" + text.encode("utf-8"))
        chk("N1 sha256(0x00||text) == paragraph leaf, sha256(text) == content hash",
            _hx(leaf) == p["leaf"].lower() and _hx(_sha(text.encode("utf-8"))) == c["content_hash"].lower(), _hx(leaf)[:18] + "...")
        try:
            ar = apply_proof(leaf, p["proof"])
        except Exception as e:  # noqa: BLE001
            ar = b""; chk("N1 paragraph proof well-formed", False, str(e))
        chk("N1 paragraph proof -> article_root", _hx(ar) == a["article_root"].lower(),
            f"recomputed {_hx(ar)[:18]}... bundle {a['article_root'][:18]}...")
        keys_ok = tuple(sorted(meta)) == META_KEYS
        chk("N2 meta has exactly the scheme's fields", keys_ok, ",".join(sorted(meta)))
        al = article_leaf(meta, ar) if keys_ok else b""
        try:
            br = apply_proof(al, a["proof"])
        except Exception as e:  # noqa: BLE001
            br = b""; chk("N2 article proof well-formed", False, str(e))
        chk("N2 article leaf (JCS meta + article_root) -> proof -> batch_root", _hx(br) == bt["root"].lower(),
            f"recomputed {_hx(br)[:18]}... bundle {bt['root'][:18]}... (URL, outlet, title, times are inside the leaf)")
        root = _hx(br) if br else bt["root"].lower()
        on = batch(root)
        chk("N3 batch root is registered on NewsRegistry", on["exists"], root)
        if on["exists"]:
            chk("N3 on-chain manifestHash == bundle manifest_hash", on["manifest_hash"] == bt["manifest_hash"].lower(), on["manifest_hash"][:18] + "...")
            if trust is not None:
                chk("N3 registrant is in YOUR trusted list (--trust)", on["registrant"].lower() in trust, on["registrant"])
            else:
                chk("N3 isTrustedRoot (registered AND registrant on the CURRENT allowlist)", is_trusted_root(rpc, reg, root),
                    f"registrant {on['registrant']}")
            t = rpc("eth_getTransactionByHash", [bt["tx"]]); r = rpc("eth_getTransactionReceipt", [bt["tx"]])
            good = bool(t and r) and (t.get("to") or "").lower() == reg.lower() and t["input"][2:10] == vc.SEL["registerBatch(bytes32,bytes32,uint32,string)"] \
                and t["input"][10:74] == root[2:] and int(r["status"], 16) == 1 and t["from"].lower() == on["registrant"].lower() \
                and any(l["address"].lower() == reg.lower() and l["topics"][0] == TOPIC_NEWS_BATCH and l["topics"][1] == root for l in r["logs"])
            blk_ts = int(rpc("eth_getBlockByNumber", [r["blockNumber"], False])["timestamp"], 16) if good else -1
            chk("N4 registration tx is the real registerBatch for this root, from the registrant, at the on-chain time",
                good and blk_ts == on["timestamp"], f"{bt['tx']} block time {blk_ts} vs on-chain {on['timestamp']}")
            chk("N5 claimed fetched_at (crawler, L0) is not after the batch block time", 0 < _ts(meta.get("fetched_at")) <= on["timestamp"],
                f"fetched_at {meta.get('fetched_at')} · published_at {meta.get('published_at')} (claimed by feed) · registered {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(on['timestamp']))} (proven)")
            if manifests:
                uri = on["uri"]
                if uri not in manifest_cache:
                    try:
                        manifest_cache[uri] = _fetch(uri)
                    except Exception as e:  # noqa: BLE001
                        manifest_cache[uri] = e
                mb = manifest_cache[uri]
                if isinstance(mb, Exception):
                    chk("N6 manifest at the on-chain URI (fetch failed, skipped)", None, f"{uri}: {mb}")
                else:
                    ok = _hx(_sha(mb)) == on["manifest_hash"]
                    listed = rebuilt = False
                    if ok:
                        m = json.loads(mb)
                        listed = {"meta": meta, "article_root": a["article_root"].lower()} in m["articles"]
                        rebuilt = _hx(merkle_root([article_leaf(x["meta"], _unhx(x["article_root"])) for x in m["articles"]])) == root
                    chk("N6 manifest at the on-chain URI: sha256 == manifestHash, lists this article, rebuilds the root", ok and listed and rebuilt,
                        f"{uri} (hash {'ok' if ok else 'MISMATCH'}, listed {listed}, root {rebuilt})")
        if outlets is not None:
            chk("U1 cited outlet is one of YOUR chosen sources (--outlets)", meta.get("outlet") in outlets, meta.get("outlet"))
        cites.append(dict(id=c["id"], outlet=meta.get("outlet"), url=meta.get("url"), title=meta.get("title"), published_at=meta.get("published_at"),
                          fetched_at=meta.get("fetched_at"), batch_root=bt["root"], registered_at=batch(root)["timestamp"] if batch(root)["exists"] else None,
                          status="PASS" if all(x[1] is not False for x in out) else "FAIL", checks=out))

    # ---- retrieval scope and "as of"
    sc = news.get("scope") or {}
    roots = [r.lower() for r in sc.get("roots", [])]
    chk2 = lambda n, ok, d="": scope_checks.append((n, None if ok is None else bool(ok), d))
    chk2("S1 scope hash == sha256(sorted batch roots)", bool(roots) and scope_hash(roots) == sc.get("scope_hash", "").lower(), f"{len(roots)} batch roots")
    chk2("S1 every cited batch root is inside the scope", all(c["batch"]["root"].lower() in roots for c in b.get("citations", [])))
    infos = {r: batch(r) for r in roots}
    if trust is not None:
        tr = all(i["exists"] and i["registrant"].lower() in trust for i in infos.values())
    else:
        tr = all(is_trusted_root(rpc, reg, r) for r in roots)
    chk2("S2 every scope root is trusted now (only trusted batches reach the model)", tr and bool(roots))
    mx = max((i["timestamp"] for i in infos.values() if i["exists"]), default=0)
    as_of_ok = _ts(b.get("as_of", "")) == mx
    chk2("S2 as_of == latest on-chain registration time in the scope", as_of_ok, f"as_of {b.get('as_of')} vs on-chain {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(mx)) if mx else '-'}")
    cfg_ok = _hx(_sha(news.get("config_json", "").encode("utf-8"))) == news.get("config_hash", "").lower()
    chk2("S3 sha256(retrieval config) == config_hash", cfg_ok, news.get("config_hash", "")[:18] + "...")
    if "inference" in b:
        try:
            sysmsg = next(m["content"] for m in json.loads(b["inference"]["request_body"])["messages"] if m["role"] == "system")
        except Exception:  # noqa: BLE001
            sysmsg = ""
        inside = all(s in sysmsg for s in (f"news_registry={reg}", f"scheme={SCHEME}", f"news_scope={sc.get('scope_hash')}",
                                           f"retrieval_config={news.get('config_hash')}", f"as_of={b.get('as_of')}"))
        chk2("S4 registry, scheme, scope hash, config hash and as_of are inside the committed request", inside,
             "fixed on-chain (requestHash) before the model call")
    return dict(header=head, citations=cites, scope=scope_checks)


def all_ok(res: dict, inf_checks: list) -> bool:
    flat = [x[1] for x in res["header"]] + [x[1] for x in res["scope"]] + [x[1] for c in res["citations"] for x in c["checks"]]
    return all(x is not False for x in flat) and all(x[1] for x in inf_checks)


def run(b: dict, rpc_url: str, trust=None, outlets=None, manifests=True, as_json=False) -> int:
    rpc = vc.RPC(rpc_url)
    chain = int(rpc("eth_chainId", []), 16)
    if chain != vc.EXPECTED_CHAIN_ID or int(b.get("chain_id", 0)) != vc.EXPECTED_CHAIN_ID:
        print(f"ERROR: chain id {chain}, bundle {b.get('chain_id')}; expected Arc testnet {vc.EXPECTED_CHAIN_ID}")
        return 1
    tr = {x.lower() for x in trust} if trust is not None else None
    res = verify_news(rpc, b, tr, set(outlets) if outlets is not None else None, manifests)
    inf = vc.verify_inference(rpc, b) if "inference" in b else []
    ok = all_ok(res, inf)
    if "inference" in b and not b.get("citations"):
        ok = ok and b.get("answer") == "Not found in registered news."
    if as_json:
        print(json.dumps(dict(news=res, model_call_checks=[dict(check=n, ok=o, detail=d) for n, o, d in inf], all_pass=ok), indent=1))
        return 0 if ok else 1
    mark = lambda o: "PASS" if o else ("SKIP" if o is None else "FAIL")
    print(f"Question: {b.get('question')}\n{b.get('as_of_statement', 'As of ' + str(b.get('as_of')))}\nAnswer:   {b.get('answer')}")
    if b.get("abstain"):
        print(f"Abstained: {b['abstain']['reason']} -- {b['abstain']['detail']}")
    for n, o, d in res["header"]:
        print(f"  [{mark(o)}] {n}" + (f"  -- {d}" if d and not o else ""))
    for c in res["citations"]:
        print(f"  [{c['status']}] {c['id']} source text from {c['outlet']} · {c['title']}\n         {c['url']}\n"
              f"         published_at {c['published_at']} (claimed by feed) · fetched_at {c['fetched_at']} (claimed by crawler, L0)")
        for n, o, d in c["checks"]:
            print(f"     [{mark(o)}] {n}" + (f"  -- {d}" if d and (o is not True or n.startswith(('N5', 'N3 isTrusted'))) else ""))
    print("Retrieval scope:")
    for n, o, d in res["scope"]:
        print(f"  [{mark(o)}] {n}" + (f"  -- {d}" if d else ""))
    if inf:
        print(f"Model call (InferenceLog {b['inference']['log_contract']}):")
        for n, o, d in inf:
            print(f"  [{mark(o)}] {n}" + (f"  -- {d}" if d and not o else ""))
    print("Not proven: that the news is true, or that no contradicting article was left out (no full-text replay for news).")
    print("RESULT:", "ALL CHECKS PASSED" if ok else "VERIFICATION FAILED", f"({rpc.n} RPC calls)")
    return 0 if ok else 1
