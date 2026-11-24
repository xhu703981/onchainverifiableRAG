"""Registration job: all unbatched 'ok' articles -> one batch -> manifest published (git branch news-manifests)
-> NewsRegistry.registerBatch on Arc TESTNET -> tx + block time stored. Usage: python -m newsreg.register"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from . import scheme as S
from .chain import EXPLORER, RPC, Signer, deployment, register_batch
from .db import DATA, connect
from .fetcher import utc_now

MANIFEST_REPO = Path(os.environ.get("NEWS_MANIFEST_REPO", DATA / "manifests-repo"))
RAW_BASE = "https://raw.githubusercontent.com/xhu703981/onchainverifiableRAG/news-manifests/manifests/"
MAX_ARTICLES = 2000


def build_manifest(arts: list[dict], created_at: str) -> tuple[bytes, list[list[bytes]]]:
    leaves = [S.article_leaf(a["meta"], S.unhx(a["article_root"])) for a in arts]
    lv = S.levels(leaves)
    man = dict(scheme=S.SCHEME, batch_root=S.hx(lv[-1][0]), article_count=len(arts), created_at=created_at,
               articles=[dict(meta=a["meta"], article_root=a["article_root"]) for a in arts])
    return S.jcs(man), lv


def publish(path: Path, msg: str) -> None:
    g = ["git", "-C", str(MANIFEST_REPO)]
    subprocess.run(g + ["add", str(path.relative_to(MANIFEST_REPO))], check=True, capture_output=True)
    subprocess.run(g + ["commit", "-q", "-m", msg], check=True, capture_output=True)
    subprocess.run(g + ["push", "-q", "origin", "news-manifests"], check=True, capture_output=True, timeout=120)


def run(min_articles: int = 1) -> dict | None:
    db = connect()
    dep = deployment()
    rows = db.execute("SELECT id, meta_json, article_root FROM articles WHERE status='ok' AND batch_id IS NULL ORDER BY id LIMIT ?",
                      (MAX_ARTICLES,)).fetchall()
    if len(rows) < min_articles:
        print(f"{utc_now()} nothing to register ({len(rows)} new articles)", flush=True)
        return None
    arts = [dict(id=r["id"], meta=json.loads(r["meta_json"]), article_root=r["article_root"]) for r in rows]
    created = utc_now()
    mbytes, lv = build_manifest(arts, created)
    root = lv[-1][0]
    mh = S.sha(mbytes)
    name = f"news-{created.replace(':', '').replace('-', '')}-{root.hex()[:10]}.json"
    path = MANIFEST_REPO / "manifests" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(mbytes)
    uri = RAW_BASE + name
    cur = db.execute("INSERT INTO batches(root,manifest_hash,manifest_path,manifest_uri,article_count,status,created_at) VALUES(?,?,?,?,?,?,?)",
                     (S.hx(root), S.hx(mh), str(path), uri, len(arts), "built", created))
    bid = cur.lastrowid
    db.commit()
    publish(path, f"batch {S.hx(root)[:18]} ({len(arts)} articles)")
    rpc = RPC()
    signer = Signer.from_env(rpc)
    r = register_batch(signer, dep["news_registry"], root, mh, len(arts), uri)
    db.execute("UPDATE batches SET tx=?, block_number=?, block_time=?, registrant=?, status='registered' WHERE id=?",
               (r["tx"], r["block"], r["block_time"], signer.address, bid))
    for i, a in enumerate(arts):
        db.execute("UPDATE articles SET batch_id=?, batch_index=?, batch_proof=? WHERE id=?", (bid, i, json.dumps(S.proof(lv, i)), a["id"]))
    db.commit()
    out = dict(batch_id=bid, root=S.hx(root), manifest_hash=S.hx(mh), articles=len(arts), uri=uri, tx=r["tx"], block=r["block"],
               block_time=r["block_time"], cost_usdc=round(r["cost_usdc"], 6), explorer=f"{EXPLORER}/tx/{r['tx']}")
    print(utc_now(), json.dumps(out), flush=True)
    return out


if __name__ == "__main__":
    run()
