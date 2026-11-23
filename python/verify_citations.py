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

If the bundle has an "inference" section (on-chain InferenceLog), it ALSO checks the model call:
  M1 the log record exists, was written by an authorized logger, and is finalized
  M2 sha256(request_body bytes) == on-chain requestHash; the body is in canonical form; model/question
     id/answer id/client timestamp inside the hashed request match the bundle and the record
  M3 every [Pn] passage in the committed prompt hashes to the committed passage list / passagesRoot,
     and every cited passage text appears verbatim in the committed prompt under its id
  M4 sha256(raw response bytes) == on-chain responseHash, and the bundle's answer and citation ids are
     exactly what that response contains
  M5 commit happened in an earlier block than finalize, and the commit/finalize txs are the real
     log txs for this answerId
It does NOT verify the model's reasoning, and cannot prove the provider (not us) produced the response.

usage:  python verify_citations.py bundle.json [--rpc URL] [--trust 0xPUBLISHER ...] [--json]
exit code: 0 if every citation PASSES, 1 otherwise.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
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
}
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
        with urllib.request.urlopen(req, timeout=30) as r:
            out = json.load(r)
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
    chk("M4 sha256(raw response bytes) == on-chain responseHash", _sha(resp_bytes) == hexw("responseHash"),
        f"recomputed {_sha(resp_bytes)[:18]}... on-chain {hexw('responseHash')[:18]}...")
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


def verify_bundle(b: dict, rpc_url: str = DEFAULT_RPC, trust: list[str] | None = None) -> dict:
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
    ok = bool(cites) and all(r["status"] == "PASS" for r in cites) and all(x[1] for x in inf)
    return dict(all_pass=ok, header=[dict(check=n, ok=o, detail=d) for n, o, d in head], citations=cites,
                model_call=[dict(check=n, ok=o, detail=d) for n, o, d in inf], owner=as_addr(rpc.call(contract, "owner()")),
                rpc_calls=rpc.n)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bundle")
    ap.add_argument("--rpc", default=DEFAULT_RPC)
    ap.add_argument("--trust", nargs="*", help="your own trusted publisher addresses (overrides the contract allowlist)")
    ap.add_argument("--json", action="store_true", help="print machine-readable results")
    a = ap.parse_args(argv)
    b = json.load(open(a.bundle, encoding="utf-8"))
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
    ok = not head_fail and results and all(r["status"] == "PASS" for r in results) and all(x[1] for x in inf_checks)
    if a.json:
        print(json.dumps(dict(chain_id=chain, contract=contract, owner=owner, errors=head_fail, results=results,
                              model_call_checks=[dict(check=n, ok=o, detail=d) for n, o, d in inf_checks], all_pass=bool(ok)), indent=1))
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
                print(f"  [{'PASS' if o else 'FAIL'}] {n}" + (f"  -- {d}" if d and not o else ""))
        print("RESULT:", ("ALL CHECKS PASSED" if inf_checks else "ALL CITATIONS VERIFIED") if ok else "VERIFICATION FAILED", f"({rpc.n} RPC calls)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
