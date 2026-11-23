"""Hybrid retrieval (BM25 + Contriever, RRF) for PoisonedRAG NQ target questions.

Corpus = full BEIR NQ (2,681,468 passages) + all 500 PoisonedRAG fake passages (100 targets x 5),
fakes formatted as in PoisonedRAG black-box: question + "." + adv_text.
 - BM25: bm25s index over corpus+fakes (title + " " + text). Clean run = same index with fake ids removed.
 - Dense: Contriever dot product. Clean = repo's shipped top-100 (reproduced exactly, see dense_fakes.py);
   poisoned = shipped top-100 merged with Contriever scores of all 500 fakes.
 - Fusion: RRF (k=60) over BM25 top-100 and dense top-100. Top-5 go to the generator.
Output: retrieval_n{N}.json"""
import json, sys, numpy as np, bm25s, Stemmer, pyarrow.parquet as pq
from pathlib import Path
D = Path(__file__).resolve().parents[1] / "data"; HERE = Path(__file__).resolve().parent
N = int(sys.argv[1]) if len(sys.argv) > 1 else 30
K, TOPN, RRF_K = 5, 100, 60
POOL = 50  # fused candidate pool kept for on-chain filtering (filter, then take top-5)
adv = json.load(open(D / "nq_adv.json")); shipped = json.load(open(D / "nq_contriever.json"))
fake_dense = json.load(open(D / f"dense_fake_scores_n{N}.json"))
qids = list(adv)[:N]
qrels = {}
for line in open(D / "nq_qrels_test.tsv").read().splitlines()[1:]:
    q, d, s = line.split("\t"); qrels.setdefault(q, set()).add(d)
ids = json.load(open(D / "bm25_doc_ids.json"))
bm = bm25s.BM25.load(str(D / "bm25_nq_poisoned"))
stem = Stemmer.Stemmer("english")
qtok = bm25s.tokenize([adv[q]["question"] for q in qids], stopwords="en", stemmer=stem, show_progress=False)
res, sc = bm.retrieve(qtok, k=TOPN + 500, show_progress=False, n_threads=8)
fake_text = {f"adv:{q}:{j}": a["question"] + "." + s for q, a in adv.items() for j, s in enumerate(a["adv_texts"])}

def rrf(*rankings):
    s = {}
    for r in rankings:
        for i, d in enumerate(r):
            s[d] = s.get(d, 0.0) + 1.0 / (RRF_K + i + 1)
    return [d for d, _ in sorted(s.items(), key=lambda x: (-x[1], x[0]))]

out, need = [], set()
for i, q in enumerate(qids):
    bm_all = [ids[j] for j in res[i]]
    bm_pois = bm_all[:TOPN]
    bm_clean = [d for d in bm_all if not d.startswith("adv:")][:TOPN]
    dn_clean = [d for d, _ in sorted(shipped[q].items(), key=lambda x: -x[1])][:TOPN]
    merged = dict(shipped[q]); merged.update(fake_dense[q])
    dn_pois = [d for d, _ in sorted(merged.items(), key=lambda x: -x[1])][:TOPN]
    rec = dict(id=q, question=adv[q]["question"], correct=adv[q]["correct answer"], target=adv[q]["incorrect answer"],
               gold_ids=sorted(qrels.get(q, [])),
               clean=dict(bm25=bm_clean[:K], dense=dn_clean[:K], hybrid=rrf(bm_clean, dn_clean)[:K]),
               poisoned=dict(bm25=bm_pois[:K], dense=dn_pois[:K], hybrid=rrf(bm_pois, dn_pois)[:K]),
               pool=dict(clean=rrf(bm_clean, dn_clean)[:POOL], poisoned=rrf(bm_pois, dn_pois)[:POOL]))
    out.append(rec)
    for c in ("clean", "poisoned"):
        for m in rec[c].values(): need.update(d for d in m if not d.startswith("adv:"))
        need.update(d for d in rec["pool"][c] if not d.startswith("adv:"))
tbl = pq.read_table(D / "nq_corpus.parquet", columns=["_id", "text"], filters=[("_id", "in", sorted(need))])
text = {r["_id"]: r["text"] for r in tbl.to_pylist()}; text.update(fake_text)
stats = {}
for rec in out:
    rec["texts"] = {d: text[d] for c in ("clean", "poisoned") for m in rec[c].values() for d in m}
    rec["texts"].update({d: text[d] for c in ("clean", "poisoned") for d in rec["pool"][c]})
    for c in ("clean", "poisoned"):
        for m, lst in rec[c].items():
            own = sum(d.startswith(f"adv:{rec['id']}:") for d in lst)
            other = sum(d.startswith("adv:") for d in lst) - own
            gold = int(any(d in rec["gold_ids"] for d in lst))
            ans = int(any(rec["correct"].lower() in text[d].lower() for d in lst if not d.startswith("adv:")))
            st = stats.setdefault(f"{c}/{m}", dict(own_fakes=0, other_fakes=0, gold_hit=0, ans_in_clean_ctx=0, q_with_any_fake=0))
            st["own_fakes"] += own; st["other_fakes"] += other; st["gold_hit"] += gold; st["ans_in_clean_ctx"] += ans
            st["q_with_any_fake"] += int(own + other > 0)
summary = {k: {"avg_own_fakes_in_top5": v["own_fakes"] / N, "avg_other_fakes_in_top5": v["other_fakes"] / N,
               "gold_recall@5": v["gold_hit"] / N, "answer_string_in_clean_ctx@5": v["ans_in_clean_ctx"] / N,
               "frac_q_with_fake_in_top5": v["q_with_any_fake"] / N} for k, v in stats.items()}
json.dump(dict(n=N, k=K, pool=POOL, rrf_k=RRF_K, topn=TOPN, summary=summary, items=out), open(HERE / f"retrieval_n{N}.json", "w"), indent=1)
for k, v in summary.items(): print(k, {a: round(b, 3) for a, b in v.items()})
