"""Demo answer step with an on-chain inference log (Arc testnet, chain 5042002 only).

 1. hybrid RAG pool (fused top-50) -> live SourceRegistry.areVerified filter (--naive: areRegistered) -> top-5 = [P1..P5]
 2. build the OpenRouter request body and serialize it CANONICALLY:
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    The system message carries answer_id, question_id and the client timestamp, so they are inside the hashed bytes.
 3. InferenceLog.commit(answerId, questionId, sha256(request bytes), passagesRoot, sha256(model), n)  -- BEFORE the call
    passagesRoot = sha256(concat(sha256(P1 text) .. sha256(Pn text)))  (32-byte digests, prompt order)
 4. POST exactly those bytes to OpenRouter (no cache), keep the exact raw response bytes
 5. InferenceLog.finalize(answerId, sha256(raw response bytes))  -- strictly later block
 6. write bundle_<qid>_logged.json with everything needed to re-check byte-for-byte.
usage: answer.py <qid> [--naive]"""
import base64, datetime, hashlib, json, os, re, secrets, sys, time, urllib.request
from pathlib import Path
from eth_account import Account
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "python")]
from registry import connect, assert_chain, fee_fields
import verify_citations as vc
SREG = "0x9350E7E458abe568B40c1Fc28c66dE674865878e"; ILOG = "0xc7CfFb4Ae006AccC82813829CCC466B964aBCAE3"
CHAIN = 5042002; RPC = "https://rpc.testnet.arc.io"; EXP = "https://explorer.testnet.arc.io"; MODEL = "openai/gpt-4o-mini"
ILOG_ABI = [
    {"type": "function", "name": "commit", "stateMutability": "nonpayable", "outputs": [],
     "inputs": [{"name": n, "type": t} for n, t in [("answerId", "bytes32"), ("questionId", "bytes32"), ("requestHash", "bytes32"),
                                                     ("passagesRoot", "bytes32"), ("modelHash", "bytes32"), ("passageCount", "uint16")]]},
    {"type": "function", "name": "finalize", "stateMutability": "nonpayable", "outputs": [],
     "inputs": [{"name": "answerId", "type": "bytes32"}, {"name": "responseHash", "type": "bytes32"}]},
]
def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
S = lambda b: hashlib.sha256(b).digest()
HX = lambda b: "0x" + b.hex()

qid = sys.argv[1]; naive = "--naive" in sys.argv
w3, sreg = connect(RPC, SREG); assert_chain(w3, CHAIN)
ilog = w3.eth.contract(address=ILOG, abi=ILOG_ABI)
env = dict(l.split("=", 1) for l in (ROOT / ".env.arc").read_text().split() if "=" in l)
acct = Account.from_key(env["PRIVATE_KEY"]); assert acct.address == env["DEPLOYER_ADDRESS"]

it = {i["id"]: i for i in json.load(open(ROOT / "experiments/hybrid_baseline/retrieval_n30.json"))["items"]}[qid]
pool = it["pool"]["poisoned"]
ok = (sreg.functions.areRegistered if naive else sreg.functions.areVerified)(
    [HX(S(it["texts"][d].encode())) for d in pool]).call()
kept = [d for d, o in zip(pool, ok) if o][:5]
passages = [dict(id=f"P{i + 1}", text=it["texts"][d], content_hash=HX(S(it["texts"][d].encode()))) for i, d in enumerate(kept)]
passages_root = S(b"".join(bytes.fromhex(p["content_hash"][2:]) for p in passages))

question_id = S(f"nq:{qid}".encode())
client_ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
answer_id = S(question_id + client_ts.encode() + secrets.token_bytes(16))
meta = f"Request metadata (audit only, do not mention): answer_id={HX(answer_id)}; question_id={HX(question_id)}; client_ts={client_ts}"
user = ("Answer the question using ONLY the numbered passages. Cite the passage ids you used.\n"
        'Reply with JSON only: {"answer": "<short answer>", "citations": ["P1", ...]}. '
        'If the passages do not contain the answer, reply {"answer": "I don\'t know", "citations": []}.\n\n'
        + "\n\n".join(f"[{p['id']}] {p['text']}" for p in passages) + f"\n\nQuestion: {it['question']}")
body = {"model": MODEL, "temperature": 0, "max_tokens": 150,
        "messages": [{"role": "system", "content": meta}, {"role": "user", "content": user}]}
req_bytes = canonical(body); req_hash = S(req_bytes)

def send(fn, note):
    tx = fn.build_transaction({"from": acct.address, "nonce": w3.eth.get_transaction_count(acct.address), "chainId": CHAIN, **fee_fields(w3)})
    r = w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(acct.sign_transaction(tx).raw_transaction))
    assert r["status"] == 1, note
    rec = dict(note=note, tx="0x" + r["transactionHash"].hex().removeprefix("0x"), block=r["blockNumber"], gasUsed=r["gasUsed"],
               effectiveGasPrice=r["effectiveGasPrice"], cost_usdc=r["gasUsed"] * r["effectiveGasPrice"] / 1e18)
    lf = ROOT / "experiments/arc/inference_txs.json"; L = json.load(open(lf)) if lf.exists() else []
    L.append(rec); json.dump(L, open(lf, "w"), indent=1); return rec

c_rec = send(ilog.functions.commit(answer_id, question_id, req_hash, passages_root, S(MODEL.encode()), len(passages)), f"commit {qid}")
req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", req_bytes,
                             {"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"], "Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=120) as r:
    resp_bytes = r.read()
received_ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
resp_hash = S(resp_bytes)
while w3.eth.block_number <= c_rec["block"]: time.sleep(0.3)
f_rec = send(ilog.functions.finalize(answer_id, resp_hash), f"finalize {qid}")

resp = json.loads(resp_bytes)
content = resp["choices"][0]["message"]["content"]
j = json.loads(re.search(r"\{.*\}", content, re.S).group(0))
idx = json.load(open(Path(__file__).with_name("tx_index.json")))
byid = {p["id"]: p for p in passages}
cites = []
for pid in j.get("citations", []):
    if pid not in byid: continue
    p = byid[pid]; h = p["content_hash"]
    exists, cid, uri, registrant, ts = sreg.functions.getSource(h).call(); reg = idx.get(h, {})
    cites.append(dict(id=pid, text=p["text"], content_hash=h, cid=vc.cid_v1_raw(bytes.fromhex(h[2:])), registrant=registrant,
                      registered_at_unix=ts, registration_tx=reg.get("tx"), block_number=reg.get("block"),
                      explorer=dict(tx=f"{EXP}/tx/{reg.get('tx')}", contract=f"{EXP}/address/{SREG}")))
bundle = dict(
    question=it["question"], answer=j.get("answer"), filter="registered-only (UNSAFE)" if naive else "verified-only",
    chain_id=CHAIN, rpc=RPC, contract=SREG, citations=cites,
    inference=dict(
        log_contract=ILOG, answer_id=HX(answer_id), question_key=f"nq:{qid}", question_id=HX(question_id), model=MODEL,
        client_request_ts=client_ts, client_response_received_ts=received_ts,
        serialization='json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")',
        request_body=req_bytes.decode("utf-8"), request_hash=HX(req_hash),
        passages=[dict(id=p["id"], content_hash=p["content_hash"]) for p in passages], passages_root=HX(passages_root),
        response_body_b64=base64.b64encode(resp_bytes).decode(), response_hash=HX(resp_hash),
        provider_generation_id=resp.get("id"), provider_model=resp.get("model"),
        commit_tx=c_rec["tx"], commit_block=c_rec["block"], finalize_tx=f_rec["tx"], finalize_block=f_rec["block"],
        explorer=dict(commit=f"{EXP}/tx/{c_rec['tx']}", finalize=f"{EXP}/tx/{f_rec['tx']}", log_contract=f"{EXP}/address/{ILOG}")),
    how_to_verify="python3 python/verify_citations.py <this file>")
name = f"bundle_{qid}_logged{'_naive' if naive else ''}.json"
json.dump(bundle, open(Path(__file__).with_name(name), "w"), indent=1, ensure_ascii=False)
print(name, "| answer:", bundle["answer"], "| cites:", [x["id"] for x in cites], "| commit", c_rec["tx"][:12], "blk", c_rec["block"],
      "| finalize", f_rec["tx"][:12], "blk", f_rec["block"], "| gas", c_rec["gasUsed"], f_rec["gasUsed"])
