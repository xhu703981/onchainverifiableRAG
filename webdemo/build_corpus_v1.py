"""Build the committed corpus artifacts (run once per corpus version).

  data/corpus_v1.jsonl           every passage the retriever searches, one canonical {"hash","text"} per line,
                                 sorted by hash = Merkle leaf order
  data/retrieval_config_v1.json  what CorpusRegistry.configHash commits to: sha256 of corpus file, retriever
                                 code, model weights and tokenizer, plus every retrieval parameter
Prints the Merkle root, leaf count and config hash to publish on Arc."""
import hashlib, json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent; D = HERE / "data"
sys.path.insert(0, str(HERE)); import verify_citations as vc  # noqa: E402

sha = lambda b: "0x" + hashlib.sha256(b).hexdigest()
canon = lambda o: json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
docs = [json.loads(l) for l in open(D / "corpus.jsonl", encoding="utf-8")]
rows = sorted(({"hash": sha(d["text"].encode("utf-8")), "text": d["text"]} for d in docs), key=lambda r: r["hash"])
assert len({r["hash"] for r in rows}) == len(rows)
(D / "corpus_v1.jsonl").write_text("".join(canon(r) + "\n" for r in rows), encoding="utf-8")
levels = vc.merkle_levels([bytes.fromhex(r["hash"][2:]) for r in rows])
root = "0x" + levels[-1][0].hex()
STOP = sorted(set("a an and are as at be by for from has he in is it its of on that the to was were will with who what when where which why how do does did".split()))
art = lambda rel: {"path": rel, "sha256": sha((D / rel).read_bytes())}
cfg = {
    "version": "hybrid-v1",
    "corpus": {**art("corpus_v1.jsonl"), "leaves": len(rows), "merkle_root": root,
               "format": "jsonl; one canonical JSON {hash,text} per line; sorted by hash (= leaf order)",
               "merkle": "sha256; leaf = sha256(0x00||passageHash); node = sha256(0x01||left||right); odd node promoted"},
    "retriever_code": art("../retrieval_v1.py"),
    "model": {"name": "minishlab/potion-base-8M", "weights": art("potion-base-8M/model.safetensors"),
              "tokenizer": art("potion-base-8M/tokenizer.json")},
    "bm25": {"k1": 0.9, "b": 0.4, "idf": "ln(1+(N-df+0.5)/(df+0.5))", "tokens": "lowercase; [a-z0-9]+; minus stopwords", "stopwords": STOP},
    "dense": {"tokenizer": "BERT uncased WordPiece", "max_tokens": 512, "drop_unk": True, "vector": "sum of token rows, L2-normalised", "similarity": "dot"},
    "fusion": {"method": "rrf", "k": 60, "list_depth": 50, "pool": 50, "order": "score quantised to 1e-9 desc, then leaf index"},
    "selection": {"filter": "SourceRegistry.areVerified", "prompt_k": 5, "rule": "first prompt_k verified candidates in rank order"},
}
raw = (json.dumps(cfg, indent=1, ensure_ascii=False) + "\n").encode("utf-8")
(D / "retrieval_config_v1.json").write_bytes(raw)
print(json.dumps({"root": root, "leaf_count": len(rows), "config_hash": sha(raw), "corpus_sha256": cfg["corpus"]["sha256"]}, indent=1))
