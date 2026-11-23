"""Build the compact demo corpus (run on the dev box, output shipped to the user's machine).
corpus.jsonl = clean passages from the 30 NQ questions' hybrid candidate pools (registered on Arc by the
trusted publisher) + all 500 PoisonedRAG fakes (registered by an UNTRUSTED attacker wallet).
emb.npy = model2vec (minishlab/potion-base-8M, 256-d, numpy-only) passage embeddings, L2-normalized.
samples.json = the 30 questions (key, question, gold answer)."""
import hashlib, json, numpy as np
from pathlib import Path
from model2vec import StaticModel
ROOT = Path(__file__).resolve().parents[1]; OUT = Path(__file__).resolve().parent / "data"
items = json.load(open(ROOT / "experiments/hybrid_baseline/retrieval_n30.json"))["items"]
adv = json.load(open(ROOT / "experiments/data/nq_adv.json"))
docs, seen = [], set()
for it in items:
    for c in ("clean", "poisoned"):
        for d in it["pool"][c]:
            if d.startswith("adv:"): continue
            t = it["texts"][d]; h = hashlib.sha256(t.encode()).hexdigest()
            if h in seen: continue
            seen.add(h); docs.append(dict(id=d, text=t, kind="clean"))
for q, a in adv.items():
    for j, s in enumerate(a["adv_texts"]):
        t = a["question"] + "." + s; h = hashlib.sha256(t.encode()).hexdigest()
        if h in seen: continue
        seen.add(h); docs.append(dict(id=f"adv:{q}:{j}", text=t, kind="fake"))
with open(OUT / "corpus.jsonl", "w", encoding="utf-8") as f:
    for d in docs: f.write(json.dumps(d, ensure_ascii=False) + "\n")
m = StaticModel.from_pretrained("minishlab/potion-base-8M"); m.save_pretrained(str(OUT / "potion-base-8M"))
E = m.encode([d["text"] for d in docs]).astype(np.float32); E /= np.linalg.norm(E, axis=1, keepdims=True) + 1e-9
np.save(OUT / "emb.npy", E.astype(np.float16))
json.dump([dict(key=f"nq:{it['id']}", question=it["question"], gold=it["correct"]) for it in items], open(OUT / "samples.json", "w"), indent=1)
print(len(docs), "docs;", sum(d["kind"] == "fake" for d in docs), "fakes; emb", E.shape)
