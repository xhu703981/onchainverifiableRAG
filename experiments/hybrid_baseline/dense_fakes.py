"""Contriever (facebook/contriever, mean pooling, dot product -- same as PoisonedRAG) scores of
ALL 500 fake passages against the evaluated queries. Clean dense results = the repo's shipped
Contriever top-100 over the full NQ corpus (results/beir_results/nq-contriever.json).
Sanity check: re-embed the shipped top-3 clean passages for a few queries and compare scores."""
import json, sys, torch, pyarrow.parquet as pq, pyarrow.compute as pc
from pathlib import Path
from transformers import AutoTokenizer, AutoModel
D = Path(__file__).resolve().parents[1] / "data"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 30
adv = json.load(open(D / "nq_adv.json")); shipped = json.load(open(D / "nq_contriever.json"))
qids = list(adv)[:N]
tok = AutoTokenizer.from_pretrained("facebook/contriever"); m = AutoModel.from_pretrained("facebook/contriever").eval()
torch.set_num_threads(8)
def emb(texts, bs=32):
    out = []
    for i in range(0, len(texts), bs):
        x = tok(texts[i:i+bs], padding=True, truncation=True, return_tensors="pt")
        with torch.no_grad():
            h = m(**x).last_hidden_state
        msk = x["attention_mask"].unsqueeze(-1).float()
        out.append((h * msk).sum(1) / msk.sum(1))
    return torch.cat(out)
Q = emb([adv[q]["question"] for q in qids])
fake_ids, fake_txt = [], []
for q, a in adv.items():
    for j, s in enumerate(a["adv_texts"]):
        fake_ids.append(f"adv:{q}:{j}"); fake_txt.append(a["question"] + "." + s)
F = emb(fake_txt)
S = (Q @ F.T).tolist()
out = {q: {fid: S[i][k] for k, fid in enumerate(fake_ids)} for i, q in enumerate(qids)}
json.dump(out, open(D / f"dense_fake_scores_n{N}.json", "w"))
# sanity check
chk = qids[:5]; need = []
for q in chk: need += [d for d, _ in sorted(shipped[q].items(), key=lambda x: -x[1])[:3]]
tbl = pq.read_table(D / "nq_corpus.parquet", columns=["_id", "title", "text"], filters=[("_id", "in", need)])
rows = {r["_id"]: r for r in tbl.to_pylist()}
for i, q in enumerate(chk):
    top = sorted(shipped[q].items(), key=lambda x: -x[1])[:3]
    for d, s in top:
        r = rows[d]
        a = (Q[i] @ emb([r["title"] + " " + r["text"]])[0]).item()
        b = (Q[i] @ emb([r["text"]])[0]).item()
        print(q, d, "shipped=%.4f title+text=%.4f text=%.4f" % (s, a, b))
