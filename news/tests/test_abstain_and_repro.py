"""Acceptance: abstain (out-of-corpus, post-cutoff) through the live engine, and extraction reproducibility
from saved raw HTML. Needs the local DB ($NEWS_DATA) and Arc RPC; no model call is made for abstains."""
import json
import random
import sqlite3

import pytest

from newsreg import scheme as S
from newsreg.db import DATA

pytestmark = pytest.mark.skipif(not (DATA / "news.db").exists(), reason="no local news DB")


@pytest.fixture(scope="module")
def engine():
    from newsreg.engine import NewsEngine
    return NewsEngine(signer=None)  # no key: proves abstains never reach the model or the chain log


@pytest.mark.parametrize("q,reason", [("What is the boiling point of tungsten?", "no_relevant_paragraph"),
                                      ("How do I bake sourdough bread at home?", "no_relevant_paragraph"),
                                      ("Who won the 2027 Super Bowl?", "post_cutoff"),
                                      ("What happened at the summit on December 15, 2026?", "post_cutoff")])
def test_abstain(engine, q, reason):
    b = engine.ask(q)["bundle"]
    assert b["answer"] == "Not found in registered news."
    assert b["abstain"]["reason"] == reason and b["citations"] == [] and "inference" not in b
    assert b["as_of_statement"].startswith("As of ")


def test_reproducible_extraction():
    from newsreg.crawl import build_article
    db = sqlite3.connect(DATA / "news.db")
    db.row_factory = sqlite3.Row
    rows = db.execute("SELECT * FROM articles WHERE status='ok' AND batch_id IS NOT NULL").fetchall()
    random.Random(7).shuffle(rows)
    rows = rows[:60]
    assert rows
    for r in rows:
        h = r["raw_sha256"][2:]
        raw = (DATA / "raw" / h[:2] / f"{h}.html").read_bytes()
        assert S.hx(S.sha(raw)) == r["raw_sha256"]
        m = json.loads(r["meta_json"])
        a = build_article(raw, url=m["url"], outlet=m["outlet"], title=m["title"], published_at=m["published_at"], fetched_at=m["fetched_at"])
        assert a["meta"] == m, r["url"]
        assert a["article_root"] == r["article_root"] and a["article_leaf"] == r["article_leaf"], r["url"]
        stored = [p[0] for p in db.execute("SELECT leaf FROM paragraphs WHERE article_id=? ORDER BY idx", (r["id"],))]
        assert stored == a["leaves"]
