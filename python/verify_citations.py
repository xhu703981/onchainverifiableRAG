#!/usr/bin/env python3
"""Independently verify the citations in an answer bundle against the public Arc testnet.

You do NOT need to trust whoever produced the answer. This script uses only the Python
standard library and plain JSON-RPC to a public endpoint, and recomputes everything itself:

  1. SHA-256 of each cited passage text == the content hash in the bundle   (text not altered)
  2. CIDv1 (raw, sha2-256) recomputed from that hash == the bundle's CID
  3. On-chain getSource(hash): the hash is registered, and the registrant matches the bundle
  4. The registrant is a trusted publisher: the contract's isTrustedPublisher(registrant),
     or, with --trust, YOUR OWN list of publisher addresses (then the contract owner's list is not used)
  5. The cited registration tx really is to this contract, was sent by the registrant, succeeded,
     contains this hash in its calldata, emitted the registry event, and its block timestamp equals
     the on-chain registration timestamp (so this tx is the one that registered it)
Also checks that the RPC is on the expected chain and that the contract address has code.

If the bundle has a "retrieval" section (corpus committed on CorpusRegistry), it ALSO checks retrieval:
  C1 the corpus version on-chain (root, leaf count, config hash) is the one the bundle names, published
     by an authorized publisher in the cited tx
  C2 sha256(retrieval config) == on-chain configHash; corpus root, config hash and sha256(ranked candidate
     list) are inside the hashed request (so the ranking was fixed before the model call)
  C3 every ranked candidate has a valid Merkle inclusion proof against the on-chain root; every prompt
     passage is a ranked candidate
  C4 the prompt is exactly the first k candidates that are verified on SourceRegistry (none skipped)
  C5 replay: fetch the committed corpus, model and retriever code (sha256-checked against the config),
     rerun retrieval for the question, and require the identical ranked list  (needs numpy; --no-replay skips)
 (on-chain InferenceLog), it ALSO checks the model call:
  M1 the log record exists, was written by an authorized logger, and is finalized
  M6 served model/provider read from the hashed response; served model must match the requested one
     (same id, or the same model pinned to a dated snapshot). Self-reported by the provider, not proven.
  M2 sha256(request_body bytes) == on-chain requestHash; the body is in canonical form; model/question
     id/answer id/client timestamp inside the hashed request match the bundle and the record
  M3 every [Pn] passage in the committed prompt hashes to the committed passage list / passagesRoot,
     and every cited passage text appears verbatim in the committed prompt under its id
  M4 sha256(raw response bytes) == on-chain responseHash, and the bundle's answer and citation ids are
     exactly what that response contains
  M5 commit happened in an earlier block than finalize, and the commit/finalize txs are the real
     log txs for this answerId
It does NOT verify the model's reasoning, and cannot prove the provider (not us) produced the response.

News bundles ("kind": "news", NewsRegistry batches) are dispatched to verify_news.py (N/S checks + the same M checks).

usage:  python verify_citations.py bundle.json [--rpc URL] [--trust 0xPUBLISHER ...] [--json]
                                    [--artifacts DIR] [--no-replay] [--outlets NAME ...] [--no-manifest]
exit code: 0 if every citation PASSES, 1 otherwise.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request

DEFAULT_RPC = "https://rpc.testnet.arc.io"
EXPECTED_CHAIN_ID = 5042002
# 4-byte selectors = first 4 bytes of keccak256(signature). Recompute with any tool,
# e.g. `cast sig "isVerified(bytes32)"`.
SEL = {
    "isVerified(bytes32)": "c181b273",
    "getSource(bytes32)": "396a4bf1",
    "isTrustedPublisher(address)": "03d84ba5",
    "owner()": "8da5cb5b",
    "registerBatch(bytes32[])": "28916b05",
    "registerSource(bytes32,string,string)": "28f3c62c",
    "getRecord(bytes32)": "213681cd",
    "isLogger(address)": "a9132b73",
    "commit(bytes32,bytes32,bytes32,bytes32,bytes32,uint16)": "c9b01262",
    "finalize(bytes32,bytes32)": "c65ff8f2",
    "corpusInfo(uint256)": "e0509c67",
    "corpusUri(uint256)": "72043b4c",
    "isPublisher(address)": "41859ac8",
    "areVerified(bytes32[])": "633fb9d6",
}
TOPIC_PUBLISHED = "0xdebe376ae487887d2f2d702990e68cfabfda127316e4bfadd2fb12543feda5f0"  # CorpusPublished(uint256,bytes32,bytes32,uint32,string,address)
TOPIC_COMMITTED = "0xadf8196e120a50637d1c52b3c736d871cbcfc2d5ca0657e25fdc896d309b109f"  # Committed(bytes32,bytes32,bytes32,bytes32,bytes32,uint16,address,uint256)
TOPIC_FINALIZED = "0x02a8371a72fc60e40fcdf94ce82c6dce89d0cb3cc9548163b5b7a708d6cde9bc"  # Finalized(bytes32,bytes32,address,uint256)
# event topic0 = keccak256(event signature); `cast sig-event "..."`
TOPIC_BATCH = "0x690acb70d32f442b76eb39528ae4a4f120c7fde32126ba7bfc99751ff55c4c79"   # BatchRegistered(address,uint256,uint256,uint256)
TOPIC_SINGLE = "0x9feea9c51f33feebec7441e03b787dd4fd9298c7f29168099e68745beaeb08a9"  # SourceRegistered(bytes32,string,address,uint256)


class RPC:
    def __init__(self, url: str) -> None:
        self.url, self.n = url, 0

    def __call__(self, method: str, params: list):
        self.n += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}).encode()
        req = urllib.request.Request(self.url, body, {"Content-Type": "application/json", "User-Agent": "verify-citations/1.0"})
        for attempt in range(7):  # the public RPC rate-limits bursts: back off and retry
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    out = json.load(r)
            except urllib.error.HTTPError as e:
                if e.code != 429 or attempt == 6:
                    raise
                out = {"error": {"code": -32005, "message": "rate limit exceeded"}}
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:  # transient network error: retry
                if attempt == 6:
                    raise
                out = {"error": {"code": -32005, "message": str(e)}}
            if not (isinstance(out.get("error"), dict) and out["error"].get("code") == -32005) or attempt == 6:
                break
            time.sleep(0.4 * 2 ** attempt)
        if "error" in out:
            raise RuntimeError(f"{method}: {out['error']}")
        return out["result"]

    def call(self, to: str, sig: str, arg_hex: str = "") -> bytes:
        res = self("eth_call", [{"to": to, "data": "0x" + SEL[sig] + arg_hex}, "latest"])
        return bytes.fromhex(res[2:])


def word(b: bytes, i: int) -> bytes:
    return b[32 * i: 32 * i + 32]


def as_int(b: bytes) -> int:
    return int.from_bytes(b, "big")


def as_addr(b: bytes) -> str:
    return "0x" + b[-20:].hex()


def dyn_string(b: bytes, offset: int) -> str:
    n = as_int(b[offset: offset + 32])
    return b[offset + 32: offset + 32 + n].decode("utf-8")


def cid_v1_raw(digest: bytes) -> str:
    return "b" + base64.b32encode(bytes.fromhex("01551220") + digest).decode().lower().rstrip("=")


def pad_addr(a: str) -> str:
    return a.lower().removeprefix("0x").rjust(64, "0")


def hashes_in_calldata(inp: str) -> tuple[str, list[str]]:
    data = bytes.fromhex(inp[2:])
    sel, args = data[:4].hex(), data[4:]
    if sel == SEL["registerBatch(bytes32[])"]:
        off = as_int(word(args, 0))
        n = as_int(args[off: off + 32])
        return "registerBatch", ["0x" + args[off + 32 + 32 * i: off + 64 + 32 * i].hex() for i in range(n)]
    if sel == SEL["registerSource(bytes32,string,string)"]:
        return "registerSource", ["0x" + word(args, 0).hex()]
    return "unknown(0x" + sel + ")", []


def verify_citation(rpc: RPC, contract: str, c: dict, my_trust: set[str] | None) -> dict:
    fails, notes = [], []
    text = c.get("text", "")
    claimed = (c.get("content_hash") or "").lower()
    h = "0x" + hashlib.sha256(text.encode("utf-8")).hexdigest()
    if h != claimed:
        fails.append(f"text was altered: sha256(text)={h[:18]}... but bundle says {claimed[:18]}...")
    # From here on we check the hash of the text we actually received.
    cid = cid_v1_raw(bytes.fromhex(h[2:]))
    if c.get("cid") and c["cid"] != cid:
        fails.append(f"CID mismatch: recomputed {cid[:20]}... bundle {c['cid'][:20]}...")

    src = rpc.call(contract, "getSource(bytes32)", h[2:])
    exists = as_int(word(src, 0)) == 1
    if not exists:
        fails.append("hash of this text is NOT registered on-chain")
        return dict(id=c.get("id"), status="FAIL", reasons=fails, recomputed_hash=h)
    onchain_cid = dyn_string(src, as_int(word(src, 1)))
    registrant = as_addr(word(src, 3))
    ts = as_int(word(src, 4))
    notes.append(f"registered on-chain by {registrant} at unix {ts}; on-chain CID {onchain_cid}")
    if onchain_cid and onchain_cid != cid:
        fails.append("on-chain CID differs from CID recomputed from the text")
    if c.get("registrant") and c["registrant"].lower() != registrant.lower():
        fails.append(f"bundle claims registrant {c['registrant']} but chain says {registrant}")

    trusted_by_contract = as_int(rpc.call(contract, "isTrustedPublisher(address)", pad_addr(registrant))) == 1
    if my_trust is not None:
        trusted = registrant.lower() in my_trust
        notes.append(f"trust decided by YOUR --trust list (contract allowlist says {trusted_by_contract})")
    else:
        trusted = trusted_by_contract
        verified = as_int(rpc.call(contract, "isVerified(bytes32)", h[2:])) == 1
        if verified != trusted:
            fails.append("isVerified disagrees with isTrustedPublisher(registrant)")
    if not trusted:
        fails.append(f"registered, but registrant {registrant} is NOT a trusted publisher")

    tx = c.get("registration_tx")
    if tx:
        t = rpc("eth_getTransactionByHash", [tx])
        rc = rpc("eth_getTransactionReceipt", [tx])
        if t is None or rc is None:
            fails.append(f"registration tx {tx} not found on this chain")
        else:
            fn, hs = hashes_in_calldata(t["input"])
            if (t.get("to") or "").lower() != contract.lower():
                fails.append("registration tx was not sent to this contract")
            if t["from"].lower() != registrant.lower():
                fails.append("registration tx sender is not the on-chain registrant")
            if int(rc["status"], 16) != 1:
                fails.append("registration tx reverted")
            if h not in hs:
                fails.append(f"hash not found in the {fn} calldata of the cited tx")
            topic = TOPIC_BATCH if fn == "registerBatch" else TOPIC_SINGLE
            if not any(l["address"].lower() == contract.lower() and l["topics"][0] == topic for l in rc["logs"]):
                fails.append("cited tx emitted no registry event from this contract")
            blk = rpc("eth_getBlockByNumber", [rc["blockNumber"], False])
            if int(blk["timestamp"], 16) != ts:
                fails.append("cited tx's block time != on-chain registration time (it did not register this hash)")
            if c.get("block_number") is not None and int(rc["blockNumber"], 16) != int(c["block_number"]):
                fails.append("bundle block_number does not match the tx's block")
            if not fails:
                notes.append(f"tx {tx[:12]}... {fn} of {len(hs)} hashes in block {int(rc['blockNumber'], 16)} includes it")
    else:
        notes.append("no registration_tx given: skipped tx/event check")
    return dict(id=c.get("id"), status="FAIL" if fails else "PASS", reasons=fails, notes=notes, recomputed_hash=h)


# ---------------------------------------------------------------- corpus Merkle tree
# leaf = sha256(0x00 || passageHash), node = sha256(0x01 || left || right); leaves sorted by passageHash;
# an odd node at the end of a level is promoted unchanged. (Same as CorpusRegistry.sol / arcverify.js.)
def merkle_leaf(passage_hash: bytes) -> bytes:
    return hashlib.sha256(b"\x00" + passage_hash).digest()


def merkle_node(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(b"\x01" + left + right).digest()


def merkle_levels(passage_hashes: list[bytes]) -> list[list[bytes]]:
    lv = [[merkle_leaf(h) for h in passage_hashes]]
    while len(lv[-1]) > 1:
        cur = lv[-1]
        lv.append([merkle_node(cur[i], cur[i + 1]) if i + 1 < len(cur) else cur[i] for i in range(0, len(cur), 2)])
    return lv


def merkle_proof(levels: list[list[bytes]], index: int) -> list[str]:
    path = []
    for cur in levels[:-1]:
        sib = index ^ 1
        if sib < len(cur):
            path.append("0x" + cur[sib].hex())
        index //= 2
    return path


def merkle_verify(passage_hash: str, index: int, leaf_count: int, path: list[str], root: str) -> bool:
    if not (0 <= index < leaf_count):
        return False
    h, n, p = merkle_leaf(bytes.fromhex(passage_hash[2:])), leaf_count, 0
    try:
        while n > 1:
            if index % 2 == 1:
                h = merkle_node(bytes.fromhex(path[p][2:]), h); p += 1
            elif index + 1 < n:
                h = merkle_node(h, bytes.fromhex(path[p][2:])); p += 1
            index //= 2; n = (n + 1) // 2
    except (IndexError, ValueError):
        return False
    return p == len(path) and "0x" + h.hex() == root.lower()


def _sha(b: bytes) -> str:
    return "0x" + hashlib.sha256(b).hexdigest()


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


PASSAGE_RE = re.compile(r"\[(P\d+)\] (.*?)(?=\n\n\[P\d+\] |\n\nQuestion: )", re.S)


def verify_inference(rpc: RPC, b: dict) -> list[tuple[str, bool, str]]:
    """Return a list of (check, ok, detail)."""
    inf = b["inference"]; out = []
    def chk(name, ok, detail=""):
        out.append((name, bool(ok), detail)); return bool(ok)
    log = inf["log_contract"]; aid = inf["answer_id"].lower()
    if not chk("M1 log contract has code", rpc("eth_getCode", [log, "latest"]) not in ("0x", "0x0"), log):
        return out
    rec = rpc.call(log, "getRecord(bytes32)", aid[2:])
    f = {k: word(rec, i) for i, k in enumerate(["questionId", "requestHash", "passagesRoot", "modelHash", "responseHash",
                                                 "logger", "passageCount", "commitBlock", "commitTime", "finalizeBlock", "finalizeTime"])}
    hexw = lambda k: "0x" + f[k].hex()
    cb, ct, fb, ft = (as_int(f[k]) for k in ("commitBlock", "commitTime", "finalizeBlock", "finalizeTime"))
    if not chk("M1 record exists for answer_id", cb != 0, aid):
        return out
    logger = as_addr(f["logger"])
    chk("M1 written by an authorized logger", as_int(rpc.call(log, "isLogger(address)", pad_addr(logger))) == 1,
        f"logger {logger}; log owner {as_addr(rpc.call(log, 'owner()'))}")
    chk("M1 finalized", fb != 0)

    req_bytes = inf["request_body"].encode("utf-8")
    rh = _sha(req_bytes)
    chk("M2 sha256(request bytes) == on-chain requestHash", rh == hexw("requestHash"), f"recomputed {rh[:18]}... on-chain {hexw('requestHash')[:18]}...")
    try:
        body = json.loads(req_bytes)
    except Exception as e:  # noqa: BLE001
        chk("M2 request body is JSON", False, str(e)); return out
    chk("M2 request bytes are canonical JSON", canonical(body) == req_bytes)
    sysmsg = next((m["content"] for m in body.get("messages", []) if m.get("role") == "system"), "")
    user = next((m["content"] for m in body.get("messages", []) if m.get("role") == "user"), "")
    chk("M2 model in request == bundle model, sha256(model) == on-chain modelHash",
        body.get("model") == inf["model"] and _sha(inf["model"].encode()) == hexw("modelHash"), body.get("model"))
    qid_ok = _sha(inf["question_key"].encode()) == inf["question_id"].lower() == hexw("questionId")
    chk("M2 question_id == sha256(question_key) == on-chain questionId", qid_ok, inf["question_key"])
    chk("M2 answer_id, question_id, client_ts are inside the hashed request",
        f"answer_id={inf['answer_id']}" in sysmsg and f"question_id={inf['question_id']}" in sysmsg
        and f"client_ts={inf['client_request_ts']}" in sysmsg)
    chk("M2 question text in request == bundle question", user.rstrip().endswith("Question: " + b.get("question", "")))

    segs = PASSAGE_RE.findall(user)
    prompt_hashes = [(pid, _sha(t.encode("utf-8"))) for pid, t in segs]
    listed = [(p["id"], p["content_hash"].lower()) for p in inf["passages"]]
    chk("M3 passages parsed from committed prompt == bundle passage list", prompt_hashes == listed,
        f"{len(prompt_hashes)} in prompt, {len(listed)} listed")
    root = _sha(b"".join(bytes.fromhex(h[2:]) for _, h in prompt_hashes))
    chk("M3 passagesRoot over prompt passages == on-chain passagesRoot and count matches",
        root == hexw("passagesRoot") and len(prompt_hashes) == as_int(f["passageCount"]), f"{len(prompt_hashes)} passages")
    for c in b.get("citations", []):
        inprompt = f"[{c['id']}] {c.get('text', '')}" in user
        inlist = (c["id"], _sha(c.get("text", "").encode("utf-8"))) in prompt_hashes
        chk(f"M3 cited {c['id']} text is verbatim in the committed prompt under that id", inprompt and inlist,
            "" if inprompt and inlist else "this passage was NOT in the prompt sent to the model")

    resp_bytes = base64.b64decode(inf["response_body_b64"])
    resp_ok = _sha(resp_bytes) == hexw("responseHash")
    chk("M4 sha256(raw response bytes) == on-chain responseHash", resp_ok,
        f"recomputed {_sha(resp_bytes)[:18]}... on-chain {hexw('responseHash')[:18]}...")
    sm = served_model(resp_bytes); match, kind = model_match(inf["model"], sm["served"] or "")
    chk("M6 served model (from the hashed response) matches the requested model", resp_ok and match,
        f"requested {inf['model']} -> served {sm['served']} ({kind}{'' if resp_ok else ', response bytes not authentic'})"
        f" · provider {sm['provider']} (self-reported)")
    try:
        content = json.loads(resp_bytes)["choices"][0]["message"]["content"]
        mj = json.loads(re.search(r"\{.*\}", content, re.S).group(0))
    except Exception as e:  # noqa: BLE001
        chk("M4 response parses", False, str(e)); return out
    chk("M4 bundle answer == answer in the logged response", mj.get("answer") == b.get("answer"),
        f"response says {mj.get('answer')!r}, bundle says {b.get('answer')!r}")
    chk("M4 bundle citation ids == citation ids in the logged response",
        [c["id"] for c in b.get("citations", [])] == [x for x in mj.get("citations", [])],
        f"response cites {mj.get('citations')}, bundle cites {[c['id'] for c in b.get('citations', [])]}")

    chk("M5 commit block < finalize block", 0 < cb < fb, f"commit block {cb} ({ct}), finalize block {fb} ({ft})")
    for name, txh, sel, topic, blk in [("commit", inf["commit_tx"], SEL["commit(bytes32,bytes32,bytes32,bytes32,bytes32,uint16)"], TOPIC_COMMITTED, cb),
                                        ("finalize", inf["finalize_tx"], SEL["finalize(bytes32,bytes32)"], TOPIC_FINALIZED, fb)]:
        t = rpc("eth_getTransactionByHash", [txh]); r = rpc("eth_getTransactionReceipt", [txh])
        good = bool(t and r) and (t.get("to") or "").lower() == log.lower() and t["input"][2:10] == sel \
            and t["input"][10:74] == aid[2:] and int(r["status"], 16) == 1 and int(r["blockNumber"], 16) == blk \
            and any(l["address"].lower() == log.lower() and l["topics"][0] == topic and l["topics"][1] == aid for l in r["logs"])
        chk(f"M5 {name} tx is the real log tx for this answer_id", good, txh)
    try:
        import datetime as _dt
        cts = _dt.datetime.strptime(inf["client_request_ts"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=_dt.timezone.utc).timestamp()
        chk("M5 client request timestamp is not after the commit (<=60 s skew)", cts <= ct + 60, f"client {cts:.1f} vs commit block time {ct}")
    except Exception as e:  # noqa: BLE001
        chk("M5 client timestamp parses", False, str(e))
    return out


# ---------------------------------------------------------------- corpus / retrieval (C checks)
_RETRIEVERS: dict = {}


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "verify-citations/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def load_artifacts(cfg: dict, config_url: str, artifacts_dir: str | None = None) -> dict:
    """Fetch every artifact the committed config names (or read it from a local dir) and sha256-check it."""
    import os
    import urllib.parse
    out = {}
    for name, a in [("corpus", cfg["corpus"]), ("retriever code", cfg["retriever_code"]),
                    ("model weights", cfg["model"]["weights"]), ("tokenizer", cfg["model"]["tokenizer"])]:
        if artifacts_dir:
            data = open(os.path.normpath(os.path.join(artifacts_dir, a["path"])), "rb").read()
        else:
            cache = os.path.join(os.path.expanduser("~"), ".cache", "verify_citations", a["sha256"][2:])
            if os.path.exists(cache):
                data = open(cache, "rb").read()
            else:
                data = _fetch(urllib.parse.urljoin(config_url, a["path"]))
                if _sha(data) == a["sha256"]:
                    os.makedirs(os.path.dirname(cache), exist_ok=True); open(cache, "wb").write(data)
        out[name] = (data, _sha(data) == a["sha256"])
    return out


def _abi_bytes32_array(hs: list[str]) -> str:
    return "%064x" % 32 + "%064x" % len(hs) + "".join(h[2:].lower() for h in hs)


def _decode_bool_array(b: bytes) -> list[bool]:
    off = as_int(word(b, 0)); n = as_int(b[off: off + 32])
    return [as_int(b[off + 32 + 32 * i: off + 64 + 32 * i]) == 1 for i in range(n)]


def replay_diff(committed: list[dict], replayed: list[dict]) -> str:
    """'' if identical (ids, order, ranks; rrf within 1e-9), else a short reason."""
    cids = [x["id"] for x in committed]
    for r, x in enumerate(replayed):
        if x["id"] not in cids:
            return f"replay ranks {x['id'][:12]}... at #{r + 1}, but the answer's list omits it"
    for r, (a, b) in enumerate(zip(replayed, committed)):
        if a["id"] != b["id"]:
            return f"rank #{r + 1} differs: replay {a['id'][:12]}..., answer {b['id'][:12]}..."
        if a.get("bm25") != b.get("bm25") or a.get("dense") != b.get("dense") or abs(a["rrf"] - float(b.get("rrf", 0))) > 1e-9:
            return f"rank #{r + 1} scores differ"
    if len(replayed) != len(committed):
        return f"replay returns {len(replayed)} candidates, the answer lists {len(committed)}"
    return ""


def verify_corpus(rpc: RPC, b: dict, replay: bool = True, artifacts_dir: str | None = None) -> list[tuple]:
    """Return a list of (check, ok, detail); ok is None for a check that was not run."""
    R = b["retrieval"]; out = []
    def chk(name, ok, detail=""):
        out.append((name, ok if ok is None else bool(ok), detail)); return bool(ok)
    reg, ver = R["corpus_registry"], int(R["corpus_version"])
    if not chk("C1 CorpusRegistry has code", rpc("eth_getCode", [reg, "latest"]) not in ("0x", "0x0"), reg):
        return out
    info = rpc.call(reg, "corpusInfo(uint256)", "%064x" % ver)
    root, cfg_hash = "0x" + word(info, 0).hex(), "0x" + word(info, 1).hex()
    leaves, publisher = as_int(word(info, 2)), as_addr(word(info, 3))
    chk("C1 on-chain corpus version matches the bundle (root, leaf count, config hash)",
        root == R["corpus_root"].lower() and leaves == int(R["leaf_count"]) and cfg_hash == R["config_hash"].lower(),
        f"v{ver} · {leaves} leaves")
    chk("C1 corpus published by an authorized publisher", as_int(rpc.call(reg, "isPublisher(address)", pad_addr(publisher))) == 1, publisher)
    rc = rpc("eth_getTransactionReceipt", [R["publish_tx"]])
    chk("C1 publish tx emitted CorpusPublished(version, root)", bool(rc) and int(rc["status"], 16) == 1 and any(
        l["address"].lower() == reg.lower() and l["topics"][0] == TOPIC_PUBLISHED and int(l["topics"][1], 16) == ver
        and l["topics"][2] == root for l in rc["logs"]), R["publish_tx"])

    cfg_raw = R["config_json"].encode("utf-8")
    chk("C2 sha256(retrieval config) == on-chain configHash", _sha(cfg_raw) == cfg_hash, f"recomputed {_sha(cfg_raw)[:18]}...")
    cfg = json.loads(cfg_raw)
    ranked_raw = R["ranked_json"].encode("utf-8"); ranked = json.loads(ranked_raw); rh = _sha(ranked_raw)
    body = json.loads(b["inference"]["request_body"]) if "inference" in b else {}
    sysmsg = next((m["content"] for m in body.get("messages", []) if m.get("role") == "system"), "")
    chk("C2 corpus, config and ranked-list hashes are inside the hashed request",
        f"corpus=v{ver}:{root}" in sysmsg and f"retrieval_config={cfg_hash}" in sysmsg and f"retrieval={rh}" in sysmsg,
        f"sha256(ranked list) {rh[:18]}...")

    proofs = R.get("proofs", [])
    bad = next((r for r, x in enumerate(ranked) if r >= len(proofs) or not merkle_verify(
        x["id"], int(proofs[r]["leaf_index"]), leaves, proofs[r]["path"], root)), None)
    chk("C3 every ranked candidate has a valid inclusion proof against the on-chain root",
        bad is None, f"{len(ranked)} of {len(ranked)} proofs valid" if bad is None
        else f"candidate #{bad + 1} {ranked[bad]['id'][:12]}... is not in the committed corpus")
    ids = [x["id"] for x in ranked]
    prompt = [p["content_hash"].lower() for p in b.get("inference", {}).get("passages", [])]
    chk("C3 every prompt passage is a ranked candidate", all(h in ids for h in prompt), f"{len(prompt)} prompt passages")
    ver_now = _decode_bool_array(rpc.call(b["contract"], "areVerified(bytes32[])", _abi_bytes32_array(ids)))
    want = [h for h, v in zip(ids, ver_now) if v][: int(cfg["selection"]["prompt_k"])]
    chk("C4 prompt = the first k verified candidates in rank order (none skipped)", want == prompt,
        f"{sum(ver_now)} of {len(ids)} candidates verified on SourceRegistry")

    if not replay:
        chk("C5 replay of the committed retrieval", None, "skipped (--no-replay)"); return out
    ub = rpc.call(reg, "corpusUri(uint256)", "%064x" % ver); uri = dyn_string(ub, as_int(word(ub, 0)))
    try:
        import numpy  # noqa: F401
        arts = load_artifacts(cfg, uri, artifacts_dir)
    except Exception as e:  # noqa: BLE001
        chk("C5 replay of the committed retrieval", None, f"not run: {type(e).__name__}"); return out
    names = [n for n, (_, ok) in arts.items() if not ok]
    if not chk("C5 fetched corpus, retriever code, weights and tokenizer match the committed config", not names,
               "sha256 match" if not names else f"{names[0]} sha256 differs from the committed config"):
        return out
    rows = [json.loads(l) for l in arts["corpus"][0].decode("utf-8").splitlines() if l.strip()]
    lv = merkle_levels([bytes.fromhex(r["hash"][2:]) for r in rows])
    chk("C5 Merkle root of the fetched corpus == on-chain root", "0x" + lv[-1][0].hex() == root and len(rows) == leaves,
        f"{len(rows)} passages")
    key = cfg_hash
    if key not in _RETRIEVERS:
        import importlib.util, os, tempfile
        tmp = os.path.join(tempfile.gettempdir(), f"retrieval_{arts['retriever code'][0] and _sha(arts['retriever code'][0])[2:14]}.py")
        open(tmp, "wb").write(arts["retriever code"][0])  # the committed, sha256-checked retriever code
        spec = importlib.util.spec_from_file_location("committed_retriever", tmp); mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        tok = json.loads(arts["tokenizer"][0])["model"]["vocab"]
        wpath = tmp + ".safetensors"; open(wpath, "wb").write(arts["model weights"][0])
        _RETRIEVERS[key] = mod.Retriever(rows, tok, mod.load_weights(wpath), cfg)
    rep = _RETRIEVERS[key].rank(R.get("query", b.get("question", "")))
    d = replay_diff(ranked, rep)
    chk("C5 replaying the committed retriever gives the same ranked candidates", not d,
        f"{len(rep)} candidates, identical ids and order" if not d else d)
    chk("C5 replay query == bundle question", R.get("query", b.get("question")) == b.get("question"))
    return out


_VERSION_SUFFIX = r"-(\d{4}-\d{2}-\d{2}|\d{8}|\d{4}|latest)"


def model_match(requested: str, served: str) -> tuple[bool, str]:
    """Compare the requested model id with the served one reported in the response.

    'exact' if identical (ignoring case), 'version' if served is the same model pinned to a dated
    snapshot (openai/gpt-4o-mini vs gpt-4o-mini-2024-07-18), else 'mismatch' (gpt-4o vs gpt-4o-mini).
    A vendor prefix only has to agree when both strings carry one."""
    rq, sv = (requested or "").strip().lower(), (served or "").strip().lower()
    if not rq or not sv:
        return False, "mismatch"
    if rq == sv:
        return True, "exact"
    rv, _, rn = rq.rpartition("/"); sv_v, _, sn = sv.rpartition("/")
    if rv and sv_v and rv != sv_v:
        return False, "mismatch"
    if rn == sn:
        return True, "exact"
    if re.fullmatch(re.escape(rn) + _VERSION_SUFFIX, sn):
        return True, "version"
    return False, "mismatch"


def served_model(resp_bytes: bytes) -> dict:
    """Served model / provider exactly as self-reported inside the hashed response bytes."""
    try:
        r = json.loads(resp_bytes)
        return dict(served=r.get("model"), provider=r.get("provider"))
    except Exception:  # noqa: BLE001
        return dict(served=None, provider=None)


def verify_bundle(b: dict, rpc_url: str = DEFAULT_RPC, trust: list[str] | None = None, replay: bool = True,
                  artifacts_dir: str | None = None) -> dict:
    """Structured version of the CLI checks (used by the web demo)."""
    rpc = RPC(rpc_url)
    chain = int(rpc("eth_chainId", []), 16)
    contract = b["contract"]
    head = [("chain id is Arc testnet 5042002", chain == EXPECTED_CHAIN_ID == int(b.get("chain_id", EXPECTED_CHAIN_ID)), f"RPC says {chain}")]
    head.append(("SourceRegistry has contract code", rpc("eth_getCode", [contract, "latest"]) not in ("0x", "0x0"), contract))
    if not all(x[1] for x in head):
        return dict(all_pass=False, header=head, citations=[], model_call=[], rpc_calls=rpc.n)
    my_trust = {x.lower() for x in trust} if trust is not None else None
    cites = [verify_citation(rpc, contract, c, my_trust) for c in b.get("citations", [])]
    inf = verify_inference(rpc, b) if "inference" in b else []
    cor = verify_corpus(rpc, b, replay, artifacts_dir) if "retrieval" in b else []
    ok = bool(cites) and all(r["status"] == "PASS" for r in cites) and all(x[1] for x in inf) and all(x[1] is not False for x in cor)
    model = None
    if "inference" in b:
        try:
            sm = served_model(base64.b64decode(b["inference"]["response_body_b64"]))
        except Exception:  # noqa: BLE001
            sm = dict(served=None, provider=None)
        m_ok, kind = model_match(b["inference"].get("model", ""), sm["served"] or "")
        model = dict(requested=b["inference"].get("model"), served=sm["served"], provider=sm["provider"], match=m_ok, kind=kind)
    return dict(all_pass=ok, model=model, header=[dict(check=n, ok=o, detail=d) for n, o, d in head], citations=cites,
                model_call=[dict(check=n, ok=o, detail=d) for n, o, d in inf],
                corpus=[dict(check=n, ok=o, detail=d) for n, o, d in cor], owner=as_addr(rpc.call(contract, "owner()")),
                rpc_calls=rpc.n)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bundle")
    ap.add_argument("--rpc", default=DEFAULT_RPC)
    ap.add_argument("--trust", nargs="*", help="your own trusted publisher addresses (overrides the contract allowlist)")
    ap.add_argument("--json", action="store_true", help="print machine-readable results")
    ap.add_argument("--artifacts", help="local dir holding the committed corpus/model files (still sha256-checked); default: fetch from the on-chain URI")
    ap.add_argument("--no-replay", action="store_true", help="skip the retrieval replay (C5)")
    ap.add_argument("--outlets", nargs="*", help="news bundles: only accept citations from these outlets (your own source choice)")
    ap.add_argument("--no-manifest", action="store_true", help="news bundles: skip fetching the published manifest (N6)")
    a = ap.parse_args(argv)
    b = json.load(open(a.bundle, encoding="utf-8"))
    if b.get("kind") == "news":  # news registry bundle: Merkle scheme news-merkle-v1 (see verify_news.py)
        import verify_news
        return verify_news.run(b, a.rpc, a.trust, a.outlets, not a.no_manifest, a.json)
    rpc = RPC(a.rpc)
    chain = int(rpc("eth_chainId", []), 16)
    contract = b["contract"]
    head_fail = []
    if chain != int(b.get("chain_id", EXPECTED_CHAIN_ID)) or chain != EXPECTED_CHAIN_ID:
        head_fail.append(f"RPC chain id {chain}, bundle says {b.get('chain_id')}, expected {EXPECTED_CHAIN_ID} (Arc testnet)")
    if rpc("eth_getCode", [contract, "latest"]) in ("0x", "0x0"):
        head_fail.append(f"no contract code at {contract}")
    owner = as_addr(rpc.call(contract, "owner()")) if not head_fail else "?"
    my_trust = {x.lower() for x in a.trust} if a.trust is not None else None
    results = [] if head_fail else [verify_citation(rpc, contract, c, my_trust) for c in b["citations"]]
    inf_checks = verify_inference(rpc, b) if ("inference" in b and not head_fail) else []
    cor = verify_corpus(rpc, b, not a.no_replay, a.artifacts) if ("retrieval" in b and not head_fail) else []
    ok = not head_fail and results and all(r["status"] == "PASS" for r in results) and all(x[1] for x in inf_checks) \
        and all(x[1] is not False for x in cor)
    if a.json:
        print(json.dumps(dict(chain_id=chain, contract=contract, owner=owner, errors=head_fail, results=results,
                              model_call_checks=[dict(check=n, ok=o, detail=d) for n, o, d in inf_checks],
                              corpus_checks=[dict(check=n, ok=o, detail=d) for n, o, d in cor], all_pass=bool(ok)), indent=1))
    else:
        print(f"Question: {b.get('question')}\nAnswer:   {b.get('answer')}")
        print(f"Chain {chain} via {a.rpc} | contract {contract} | allowlist owner {owner}")
        for e in head_fail:
            print("  ERROR:", e)
        for r in results:
            print(f"  [{r['status']}] citation {r['id']}  sha256={r['recomputed_hash'][:18]}...")
            for x in r["reasons"]:
                print("         x", x)
            for x in r.get("notes", []):
                print("         -", x)
        if inf_checks:
            print(f"Model call (InferenceLog {b['inference']['log_contract']}, answer_id {b['inference']['answer_id'][:18]}...):")
            for n, o, d in inf_checks:
                print(f"  [{'PASS' if o else 'FAIL'}] {n}" + (f"  -- {d}" if d and (not o or n.startswith("M6")) else ""))
        if cor:
            r = b["retrieval"]
            print(f"Corpus (CorpusRegistry {r['corpus_registry']}, v{r['corpus_version']}, root {r['corpus_root'][:18]}...):")
            for n, o, d in cor:
                print(f"  [{'PASS' if o else ('SKIP' if o is None else 'FAIL')}] {n}" + (f"  -- {d}" if d else ""))
        print("RESULT:", ("ALL CHECKS PASSED" if inf_checks else "ALL CITATIONS VERIFIED") if ok else "VERIFICATION FAILED", f"({rpc.n} RPC calls)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
