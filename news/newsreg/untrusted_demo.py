"""FAIL-case fixture: a throwaway key that is NOT on the allowlist registers a real batch (a subset of a
trusted batch's articles, so a different root) on Arc TESTNET. Writes examples/untrusted_batch.json.
The throwaway key is generated locally and stored in $NEWS_DATA/untrusted.key (chmod 600), never printed."""
import json
import os
import secrets

from . import scheme as S
from .chain import EXPLORER, RPC, Signer, deployment, register_batch
from .db import DATA, connect
from .register import build_manifest

OUT = __import__("pathlib").Path(__file__).resolve().parent.parent / "examples" / "untrusted_batch.json"


def main(article_ids: list[int]) -> None:
    rpc = RPC()
    kp = DATA / "untrusted.key"
    if not kp.exists():
        kp.write_text("0x" + secrets.token_hex(32))
        os.chmod(kp, 0o600)
    bad = Signer(kp.read_text().strip(), rpc)
    owner = Signer.from_env(rpc)
    if int(rpc("eth_getBalance", [bad.address, "latest"]), 16) < 2 * 10**16:
        owner.send(bad.address, b"", value=5 * 10**16)  # 0.05 testnet USDC for gas
    db = connect()
    rows = [db.execute("SELECT id, meta_json, article_root FROM articles WHERE id=?", (i,)).fetchone() for i in article_ids]
    arts = [dict(id=r["id"], meta=json.loads(r["meta_json"]), article_root=r["article_root"]) for r in rows]
    mbytes, lv = build_manifest(arts, "2026-11-24T00:00:00Z")
    root, mh = lv[-1][0], S.sha(mbytes)
    r = register_batch(bad, deployment()["news_registry"], root, mh, len(arts), "untrusted-demo://not-published")
    out = dict(note="registered by an address that is NOT a trusted publisher; isTrustedRoot(root) == false", registrant=bad.address,
               root=S.hx(root), manifest_hash=S.hx(mh), article_count=len(arts), tx=r["tx"], block=r["block"], block_time=r["block_time"],
               explorer=f"{EXPLORER}/tx/{r['tx']}",
               articles=[dict(article_id=a["id"], article_root=a["article_root"], index=i, proof=S.proof(lv, i)) for i, a in enumerate(arts)])
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({k: v for k, v in out.items() if k != "articles"}, indent=1))


if __name__ == "__main__":
    import sys
    main([int(x) for x in sys.argv[1:]])
