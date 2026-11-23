"""On-chain-filtered hybrid RAG reading the LIVE Arc testnet SourceRegistry.
For each question: take the fused hybrid candidate pool (top-50, fakes included), hash every passage
(SHA-256 of the exact text), ONE eth_call areVerified(hashes) [mode=verified] or areRegistered(hashes)
[mode=registered], keep passing candidates in rank order, take top-5 (filtered slots are backfilled),
generate with openai/gpt-4o-mini, judge with gpt-4o-mini.
usage: onchain_rag.py <label> <verified|registered>"""
import hashlib, json, re, statistics, sys, time, concurrent.futures as cf
from pathlib import Path
from common import chat, text_of, cost_of, clean_str, RAG_PROMPT, JUDGE, JUDGE_PROMPT
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT / "python"))
from registry import connect, assert_chain
HERE = Path(__file__).resolve().parent
REG = "0x9350E7E458abe568B40c1Fc28c66dE674865878e"; GEN = "openai/gpt-4o-mini"; K = 5
label, mode = sys.argv[1], sys.argv[2]
w3, c = connect("https://rpc.testnet.arc.io", REG); assert_chain(w3, 5042002)
ret = json.load(open(HERE / "retrieval_n30.json")); items = ret["items"]
fn = c.functions.areVerified if mode == "verified" else c.functions.areRegistered
block = w3.eth.block_number
recs = []
for it in items:
    pool = it["pool"]["poisoned"]; hs = ["0x" + hashlib.sha256(it["texts"][d].encode()).hexdigest() for d in pool]
    t = time.perf_counter(); ok = fn(hs).call(); ms = (time.perf_counter() - t) * 1000
    kept = [d for d, o in zip(pool, ok) if o][:K]
    recs.append(dict(it=it, kept=kept, ms=ms, n_pass=sum(ok), fakes_pass=sum(o for d, o in zip(pool, ok) if d.startswith("adv:")),
                     fakes_in_pool=sum(d.startswith("adv:") for d in pool)))
def run(r):
    it = r["it"]
    if r["kept"]:
        prompt = RAG_PROMPT.replace("[question]", it["question"]).replace("[context]", "\n".join(it["texts"][d] for d in r["kept"]))
    else:
        prompt = RAG_PROMPT.replace("[question]", it["question"]).replace("[context]", "")
    g = chat(GEN, prompt); resp = text_of(g)
    jr = chat(JUDGE, JUDGE_PROMPT.format(q=it["question"], gold=it["correct"], target=it["target"], resp=resp), max_tokens=60)
    jt = text_of(jr)
    try: lab = json.loads(re.search(r"\{.*\}", jt, re.S).group(0))["label"].strip().upper()
    except Exception: lab = "PARSE_ERROR"
    a = clean_str(resp)
    return dict(id=it["id"], question=it["question"], correct=it["correct"], target=it["target"], kept=r["kept"],
                n_kept=len(r["kept"]), fakes_in_top5=sum(d.startswith("adv:") for d in r["kept"]),
                gold_hit=int(any(d in it["gold_ids"] for d in r["kept"])), latency_ms=r["ms"], n_pass_pool=r["n_pass"],
                fakes_pass_pool=r["fakes_pass"], fakes_in_pool=r["fakes_in_pool"], response=resp, judge=lab, judge_raw=jt,
                sub_correct=clean_str(it["correct"]) in a, sub_target=clean_str(it["target"]) in a, cost=cost_of(g) + cost_of(jr))
with cf.ThreadPoolExecutor(10) as ex: rows = list(ex.map(run, recs))
n = len(rows); L = lambda x: sum(r["judge"] == x for r in rows) / n; lat = [r["latency_ms"] for r in rows]
summ = dict(label=label, mode=mode, n=n, block_at_start=block, correct=L("CORRECT"), planted=L("TARGET"), other_wrong=L("OTHER_WRONG"),
            idk=L("ABSTAIN"), hallucination=L("TARGET") + L("OTHER_WRONG"), asr_substr=sum(r["sub_target"] for r in rows) / n,
            gold_recall5=sum(r["gold_hit"] for r in rows) / n, avg_fakes_top5=sum(r["fakes_in_top5"] for r in rows) / n,
            avg_passages_top5=sum(r["n_kept"] for r in rows) / n, q_with_lt5_passages=sum(r["n_kept"] < 5 for r in rows),
            fakes_passing_filter=sum(r["fakes_pass_pool"] for r in rows), fakes_in_pools=sum(r["fakes_in_pool"] for r in rows),
            latency_ms_median=statistics.median(lat), latency_ms_p90=sorted(lat)[int(0.9 * n) - 1], latency_ms_max=max(lat),
            hashes_per_call=len(items[0]["pool"]["poisoned"]), cost_usd=sum(r["cost"] for r in rows), parse_errors=L("PARSE_ERROR") * n)
json.dump(dict(summary=summ, rows=rows), open(HERE / f"onchain_{label}.json", "w"), indent=1)
print(json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in summ.items()}))
