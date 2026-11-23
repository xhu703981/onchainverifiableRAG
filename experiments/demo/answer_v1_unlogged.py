"""Demo answer step: verified-only hybrid RAG + gpt-4o-mini, emitting a citation bundle (JSON)
that anyone can check with python/verify_citations.py.
usage: answer.py <qid> [--naive]   (--naive = registered-only filter, i.e. what an unsafe service would do)"""
import hashlib, json, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "python"), str(ROOT / "experiments/hybrid_baseline")]
from common import chat, text_of
from registry import connect, assert_chain
import verify_citations as vc
REG = "0x9350E7E458abe568B40c1Fc28c66dE674865878e"; CHAIN = 5042002; RPC = "https://rpc.testnet.arc.io"
EXP = "https://explorer.testnet.arc.io"
qid = sys.argv[1]; naive = "--naive" in sys.argv
w3, c = connect(RPC, REG); assert_chain(w3, CHAIN)
it = {i["id"]: i for i in json.load(open(ROOT / "experiments/hybrid_baseline/retrieval_n30.json"))["items"]}[qid]
H = lambda t: "0x" + hashlib.sha256(t.encode("utf-8")).hexdigest()
pool = it["pool"]["poisoned"]
ok = (c.functions.areRegistered if naive else c.functions.areVerified)([H(it["texts"][d]) for d in pool]).call()
kept = [d for d, o in zip(pool, ok) if o][:5]
passages = {f"P{i + 1}": it["texts"][d] for i, d in enumerate(kept)}

# hash -> registration tx index, built from the tx list by decoding calldata over public RPC (cached)
idx_f = Path(__file__).with_name("tx_index.json")
if idx_f.exists():
    idx = json.load(open(idx_f))
else:
    rpc = vc.RPC(RPC); idx = {}
    for r in json.load(open(ROOT / "experiments/arc/registrations.json")):
        t = rpc("eth_getTransactionByHash", [r["tx"]])
        if (t.get("to") or "").lower() != REG.lower(): continue
        for h in vc.hashes_in_calldata(t["input"])[1]:
            idx.setdefault(h, dict(tx=r["tx"], block=int(t["blockNumber"], 16)))
    json.dump(idx, open(idx_f, "w"))

prompt = ("Answer the question using ONLY the numbered passages. Cite the passage ids you used.\n"
          'Reply with JSON only: {"answer": "<short answer>", "citations": ["P1", ...]}. '
          'If the passages do not contain the answer, reply {"answer": "I don\'t know", "citations": []}.\n\n'
          + "\n\n".join(f"[{k}] {v}" for k, v in passages.items()) + f"\n\nQuestion: {it['question']}")
out = text_of(chat("openai/gpt-4o-mini", prompt, max_tokens=150))
j = json.loads(re.search(r"\{.*\}", out, re.S).group(0))
cites = []
for pid in j.get("citations", []):
    if pid not in passages: continue  # model cited a non-existent id: dropped
    text = passages[pid]; h = H(text)
    exists, cid, uri, registrant, ts = c.functions.getSource(h).call()
    reg = idx.get(h, {})
    cites.append(dict(id=pid, text=text, content_hash=h, cid=vc.cid_v1_raw(bytes.fromhex(h[2:])), registrant=registrant,
                      registered_at_unix=ts, registration_tx=reg.get("tx"), block_number=reg.get("block"),
                      explorer=dict(tx=f"{EXP}/tx/{reg.get('tx')}", block=f"{EXP}/block/{reg.get('block')}",
                                    registrant=f"{EXP}/address/{registrant}", contract=f"{EXP}/address/{REG}?tab=read_contract")))
bundle = dict(question=it["question"], answer=j.get("answer"), filter="registered-only (UNSAFE)" if naive else "verified-only",
              generator="openai/gpt-4o-mini", chain_id=CHAIN, rpc=RPC, contract=REG, citations=cites,
              how_to_verify="python python/verify_citations.py <this file>")
name = f"bundle_{qid}{'_naive' if naive else ''}.json"
json.dump(bundle, open(Path(__file__).with_name(name), "w"), indent=1, ensure_ascii=False)
print(name, "| answer:", bundle["answer"], "| cites:", [x["id"] for x in cites])
