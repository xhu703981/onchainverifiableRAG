"""Build a BM25 index (bm25s, Lucene-style params k1=0.9,b=0.4, English stemmer + stopwords)
over the FULL BEIR NQ corpus (2,681,468 passages) PLUS all 500 PoisonedRAG fake passages.
Doc text = title + " " + text for clean passages; fakes = question + "." + adv_text (PoisonedRAG black-box format, no title).
Output: data/bm25_nq_poisoned/ (index) + data/bm25_doc_ids.json (row -> doc id; fakes are 'adv:<qid>:<j>')."""
import json, time, pyarrow.parquet as pq, bm25s, Stemmer
from pathlib import Path
D = Path(__file__).resolve().parents[1] / "data"
t = time.time()
tbl = pq.read_table(D / "nq_corpus.parquet", columns=["_id", "title", "text"])
ids = tbl.column("_id").to_pylist(); titles = tbl.column("title").to_pylist(); texts = tbl.column("text").to_pylist()
del tbl
docs = [(ti + " " + tx) if ti else tx for ti, tx in zip(titles, texts)]
del titles, texts
adv = json.load(open(D / "nq_adv.json"))
for qid, a in adv.items():
    for j, s in enumerate(a["adv_texts"]):
        ids.append(f"adv:{qid}:{j}"); docs.append(a["question"] + "." + s)
print("docs", len(docs), "load s", round(time.time() - t)); 
stem = Stemmer.Stemmer("english")
tok = bm25s.tokenize(docs, stopwords="en", stemmer=stem, show_progress=False)
del docs
print("tokenized s", round(time.time() - t), flush=True)
r = bm25s.BM25(k1=0.9, b=0.4)
r.index(tok, show_progress=False)
r.save(str(D / "bm25_nq_poisoned"))
json.dump(ids, open(D / "bm25_doc_ids.json", "w"))
print("done s", round(time.time() - t))
