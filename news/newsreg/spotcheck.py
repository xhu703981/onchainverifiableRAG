"""Extraction spot-check: N random batched articles; prints first/last paragraphs and boilerplate-pattern hits
for manual review, plus per-outlet status counts (ok / short / empty / error)."""
import random
import re
import sys

from .db import connect

BOILER = re.compile(r"(?i)^(advertisement|related( stories| articles|:)|read more|sign up|subscribe|follow us|share (this|on)|"
                    r"click here|watch:|listen:|image (caption|source)|getty images|copyright|all rights reserved|"
                    r"more from|recommended|you may also like|newsletter|cookie|skip to|log ?in|download the app)\b|"
                    r"(sign up for|subscribe to our|newsletter|©)")


def main(n=30, seed=2026):
    db = connect()
    rows = db.execute("SELECT id, outlet, title, url FROM articles WHERE status='ok' AND batch_id IS NOT NULL").fetchall()
    sample = random.Random(seed).sample(rows, min(n, len(rows)))
    for r in sample:
        ps = [x[0] for x in db.execute("SELECT text FROM paragraphs WHERE article_id=? ORDER BY idx", (r["id"],))]
        hits = [(i, p) for i, p in enumerate(ps) if BOILER.search(p) and len(p) < 300]
        print(f"\n##### [{r['id']}] {r['outlet']} | {r['title'][:90]} | {len(ps)} paragraphs\n{r['url']}")
        for i, p in list(enumerate(ps))[:3] + [("…", "")] + list(enumerate(ps))[-3:]:
            print(f"  {i}: {p[:160]}")
        for i, p in hits:
            print(f"  FLAG {i}: {p[:200]}")
    print("\n##### status per outlet")
    for r in db.execute("SELECT outlet, SUM(status='ok') ok, SUM(status='short') short, SUM(status='empty') empty, SUM(status='error') err, "
                        "SUM(status='duplicate') dup, COUNT(*) n FROM articles GROUP BY outlet ORDER BY outlet"):
        print(dict(r))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 30)
