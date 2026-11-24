"""Registration job: all unbatched 'ok' articles -> one batch -> manifest published (git branch news-manifests)
-> NewsRegistry.registerBatch on Arc TESTNET -> tx + block time stored. Usage: python -m newsreg.register

Idempotent: a batch is first recorded as 'built' (articles assigned, manifest file written) in one DB transaction.
Every run first finishes any 'built' batch: publish (no-op if already pushed), then register unless the root is
already on-chain from our key (a crash after the tx was mined), so a batch is never registered twice."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from . import scheme as S
from .chain import EXPLORER, RPC, Signer, call, deployment, register_batch
from .db import DATA, connect
from .fetcher import utc_now

MANIFEST_REPO = Path(os.environ.get("NEWS_MANIFEST_REPO", DATA / "manifests-repo"))
RAW_BASE = "https://raw.githubusercontent.com/xhu703981/onchainverifiableRAG/news-manifests/manifests/"
MAX_ARTICLES = 2000
TOPIC_BATCH = "0x381fea40433275a331b047dcd8053f7b4916bce5e7bf4653754bcfe15c6e3bd2"

def build_manifest(arts: list[dict], created_at: str) -> tuple[bytes, list[list[bytes]]]:
    leaves = [S.article_leaf(a["meta"], S.unhx(a["article_root"])) for a in arts]
    lv = S.levels(leaves)
    man = dict(scheme=S.SCHEME, batch_root=S.hx(lv[-1][0]), article_count=len(arts), created_at=created_at,
               articles=[dict(meta=a["meta"], article_root=a["article_root"]) for a in arts])
    return S.jcs(man), lv

def publish(path: Path, msg: str) -> None:
    """git add + commit (only if something changed) + push; safe to repeat."""
    g = ["git", "-C", str(MANIFEST_REPO)]
    subprocess.run(g + ["add", str(path.relative_to(MANIFEST_REPO))], check=True, capture_output=True)
    if subprocess.run(g + ["diff", "--cached", "--quiet"]).returncode != 0:
        subprocess.run(g + ["commit", "-q", "-m", msg], check=True, capture_output=True)
    subprocess.run(g + ["push", "-q", "origin", "news-manifests"], check=True, capture_output=True, timeout=120)

class ArcChain:
    """NewsRegistry on Arc testnet, with the crawler key."""

    def __init__(self) -> None:
        self.rpc = RPC()
        self.signer = Signer.from_env(self.rpc)
        self.address = self.signer.address
        self.dep = deployment()
        self.registry = self.dep["news_registry"]

    def lookup(self, root: str) -> dict | None:
        ex, _mh, registrant, _n, ts, _uri = call(self.rpc, self.registry, "getBatch(bytes32)", ["bytes32"], [S.unhx(root)],
                                                 ["bool", "bytes32", "address", "uint32", "uint64", "string"])
        if not ex:
            return None
        head = int(self.rpc("eth_blockNumber", []), 16)
        hi = head
        while hi > self.dep["deploy_block"]:  # find the registering tx, newest first, in RPC-sized windows
            lo = max(self.dep["deploy_block"], hi - 5000)
            logs = self.rpc("eth_getLogs", [{"address": self.registry, "topics": [TOPIC_BATCH, root.lower()],
                                             "fromBlock": hex(lo), "toBlock": hex(hi)}])
            for lg in reversed(logs):
                return dict(tx=lg["transactionHash"], block=int(lg["blockNumber"], 16), block_time=ts, registrant=registrant)
            hi = lo - 1
        return dict(tx=None, block=None, block_time=ts, registrant=registrant)

    def register(self, root: bytes, mh: bytes, count: int, uri: str) -> dict:
        r = register_batch(self.signer, self.registry, root, mh, count, uri)
        return dict(tx=r["tx"], block=r["block"], block_time=r["block_time"], registrant=self.address, cost_usdc=r["cost_usdc"])

def build(db, min_articles: int) -> None:
    rows = db.execute("SELECT id, meta_json, article_root FROM articles WHERE status='ok' AND batch_id IS NULL ORDER BY id LIMIT ?",
                      (MAX_ARTICLES,)).fetchall()
    if len(rows) < min_articles:
        return
    arts = [dict(id=r["id"], meta=json.loads(r["meta_json"]), article_root=r["article_root"]) for r in rows]
    created = utc_now()
    mbytes, lv = build_manifest(arts, created)
    root = lv[-1][0]
    name = f"news-{created.replace(':', '').replace('-', '')}-{root.hex()[:10]}.json"
    path = MANIFEST_REPO / "manifests" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(mbytes)
    tmp.replace(path)
    with db:
        bid = db.execute("INSERT INTO batches(root,manifest_hash,manifest_path,manifest_uri,article_count,status,created_at) VALUES(?,?,?,?,?,?,?)",
                         (S.hx(root), S.hx(S.sha(mbytes)), str(path), RAW_BASE + name, len(arts), "built", created)).lastrowid
        db.executemany("UPDATE articles SET batch_id=?, batch_index=?, batch_proof=? WHERE id=?",
                       [(bid, i, json.dumps(S.proof(lv, i)), a["id"]) for i, a in enumerate(arts)])

def finish(db, b, chain, publish_fn) -> dict:
    path = Path(b["manifest_path"])
    mbytes = path.read_bytes()
    if S.hx(S.sha(mbytes)) != b["manifest_hash"]:
        raise RuntimeError(f"manifest file changed on disk: {path}")
    publish_fn(path, f"batch {b['root'][:18]} ({b['article_count']} articles)")
    rec = chain.lookup(b["root"])
    if not rec or rec["registrant"].lower() != chain.address.lower():
        rec = chain.register(S.unhx(b["root"]), S.unhx(b["manifest_hash"]), b["article_count"], b["manifest_uri"])
    with db:
        db.execute("UPDATE batches SET tx=?, block_number=?, block_time=?, registrant=?, status='registered' WHERE id=?",
                   (rec["tx"], rec["block"], rec["block_time"], rec["registrant"], b["id"]))
    return dict(batch_id=b["id"], root=b["root"], manifest_hash=b["manifest_hash"], articles=b["article_count"], uri=b["manifest_uri"],
                tx=rec["tx"], block=rec["block"], block_time=rec["block_time"], cost_usdc=rec.get("cost_usdc"),
                explorer=f"{EXPLORER}/tx/{rec['tx']}")

def run(min_articles: int = 1, db=None, chain=None, publish_fn=None) -> dict | None:
    db = db or connect()
    publish_fn = publish_fn or publish
    if not db.execute("SELECT 1 FROM batches WHERE status='built'").fetchone():
        build(db, min_articles)
    pending = db.execute("SELECT * FROM batches WHERE status='built' ORDER BY id").fetchall()
    if not pending:
        print(f"{utc_now()} nothing to register", flush=True)
        return None
    chain = chain or ArcChain()
    out = None
    for b in pending:
        out = finish(db, b, chain, publish_fn)
        print(utc_now(), json.dumps(out), flush=True)
    return out

if __name__ == "__main__":
    run()
