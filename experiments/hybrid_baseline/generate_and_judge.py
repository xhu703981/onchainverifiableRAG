"""Baseline hallucination run: no RAG vs clean hybrid RAG vs poisoned hybrid RAG.
Generators via OpenRouter (temperature 0), PoisonedRAG prompt (src/prompts.py MULTIPLE_PROMPT).
Judge: openai/gpt-4o-mini, labels CORRECT / TARGET / OTHER_WRONG / ABSTAIN.
All API responses cached in experiments/cache/openrouter/<sha256>.json."""
import json, sys, concurrent.futures as cf
from pathlib import Path
from common import *
HERE = Path(__file__).resolve().parent
N = int(sys.argv[1]) if len(sys.argv) > 1 else 30
ret = json.load(open(HERE / f"retrieval_n{N}.json"))
items = ret["items"]
def prompt_for(it, cond):
    if cond == "no_rag": return NORAG_PROMPT.replace("[question]", it["question"])
    ids = it["clean" if cond == "clean_hybrid" else "poisoned"]["hybrid"]
    ctx = "\n".join(it["texts"][d] for d in ids)  # PoisonedRAG: contexts joined by newline
    return RAG_PROMPT.replace("[question]", it["question"]).replace("[context]", ctx)

jobs = [(m, it, c) for m in MODELS for it in items for c in CONDS]
def run(job):
    m, it, c = job
    g = chat(m, prompt_for(it, c)); resp = text_of(g)
    jr = chat(JUDGE, JUDGE_PROMPT.format(q=it["question"], gold=it["correct"], target=it["target"], resp=resp), max_tokens=60)
    jt = text_of(jr)
    try: label = json.loads(re.search(r"\{.*\}", jt, re.S).group(0))["label"].strip().upper()
    except Exception: label = "PARSE_ERROR"
    a = clean_str(resp)
    return dict(model=m, cond=c, id=it["id"], question=it["question"], correct=it["correct"], target=it["target"],
                response=resp, judge=label, judge_raw=jt,
                sub_correct=clean_str(it["correct"]) in a, sub_target=clean_str(it["target"]) in a,
                regex_abstain=bool(ABSTAIN_RE.search(resp)),
                fakes_in_ctx=sum(d.startswith("adv:") for d in it["poisoned"]["hybrid"]) if c == "poisoned_hybrid" else 0,
                cost=cost_of(g) + cost_of(jr))
with cf.ThreadPoolExecutor(12) as ex:
    rows = list(ex.map(run, jobs))
json.dump(rows, open(HERE / f"rows_n{N}.json", "w"), indent=1)

table = []
for m in MODELS:
    for c in CONDS:
        rs = [r for r in rows if r["model"] == m and r["cond"] == c]; n = len(rs)
        lab = lambda L: sum(r["judge"] == L for r in rs) / n
        table.append(dict(model=m, cond=c, n=n,
            acc_judge=lab("CORRECT"), asr_judge=lab("TARGET"), other_wrong=lab("OTHER_WRONG"), abstain=lab("ABSTAIN"),
            halluc_rate=lab("TARGET") + lab("OTHER_WRONG"),
            acc_substr=sum(r["sub_correct"] for r in rs) / n,
            asr_substr=sum(r["sub_target"] for r in rs) / n,   # PoisonedRAG's ASR definition
            parse_errors=sum(r["judge"] == "PARSE_ERROR" for r in rs)))
json.dump(dict(n=N, models=MODELS, judge=JUDGE, table=table, total_cost_usd_reported=sum(r["cost"] for r in rows),
               retrieval_summary=ret["summary"]), open(HERE / f"baseline_results_n{N}.json", "w"), indent=1)
print(f"{'model':34s} {'cond':16s} n  acc_j  asr_j  other  abst  halluc | acc_sub asr_sub")
for t in table:
    print(f"{t['model']:34s} {t['cond']:16s} {t['n']} {t['acc_judge']:.2f}  {t['asr_judge']:.2f}  {t['other_wrong']:.2f}  {t['abstain']:.2f}  {t['halluc_rate']:.2f}  | {t['acc_substr']:.2f}   {t['asr_substr']:.2f}  pe={t['parse_errors']}")
print("reported cost (this run, incl. cache hits counted once) $%.4f" % sum(r["cost"] for r in rows))
