"""The registration job must converge however often it runs and wherever a previous run stopped
(principle: make operations idempotent). Chain and git are faked; the DB is a temp SQLite file."""
import json

import pytest

from newsreg import register as R
from newsreg import scheme as S
from newsreg.db import connect


class FakeChain:
    address = "0xCrawler"

    def __init__(self):
        self.onchain, self.sent, self.crash_after_send = {}, 0, False

    def lookup(self, root):
        return self.onchain.get(root)

    def register(self, root, mh, count, uri):
        self.sent += 1
        rec = dict(tx=f"0xtx{self.sent}", block=100 + self.sent, block_time=1_800_000_000 + self.sent, registrant=self.address)
        self.onchain[S.hx(root)] = rec
        if self.crash_after_send:
            raise ConnectionError("process killed after the tx was mined")
        return rec


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "MANIFEST_REPO", tmp_path / "repo")
    db = connect(tmp_path / "news.db")
    for i in range(3):
        meta = dict(url=f"https://e.com/{i}", outlet="E", title=f"t{i}", published_at=None, fetched_at="2026-11-24T00:00:00Z",
                    extractor_version="x", normalization="0x00", raw_html_sha256="0x11", version=1)
        ar = S.hx(S.para_leaf(f"p{i}"))
        db.execute("INSERT INTO articles(url,outlet,fetched_at,raw_sha256,meta_json,article_root,status) VALUES(?,?,?,?,?,?,?)",
                   (meta["url"], "E", meta["fetched_at"], "0x11", S.jcs(meta).decode(), ar, "ok"))
    db.commit()
    published = []
    return db, FakeChain(), (lambda path, msg: published.append(path.name)), published


def test_normal_run_then_rerun_is_noop(env):
    db, chain, pub, published = env
    out = R.run(db=db, chain=chain, publish_fn=pub)
    assert out["articles"] == 3 and chain.sent == 1
    assert R.run(db=db, chain=chain, publish_fn=pub) is None and chain.sent == 1


def test_crash_after_tx_mined_reconciles_without_second_registration(env):
    db, chain, pub, published = env
    chain.crash_after_send = True
    with pytest.raises(ConnectionError):
        R.run(db=db, chain=chain, publish_fn=pub)
    chain.crash_after_send = False
    R.run(db=db, chain=chain, publish_fn=pub)
    rows = db.execute("SELECT status, tx FROM batches").fetchall()
    assert [tuple(r) for r in rows] == [("registered", "0xtx1")]
    assert chain.sent == 1
    assert db.execute("SELECT COUNT(*) FROM articles WHERE batch_id IS NULL").fetchone()[0] == 0


def test_crash_before_tx_registers_the_same_batch(env):
    db, chain, pub, published = env

    def boom(path, msg):
        published.append(path.name)
        raise OSError("git push failed")
    with pytest.raises(OSError):
        R.run(db=db, chain=chain, publish_fn=boom)
    out = R.run(db=db, chain=chain, publish_fn=pub)
    rows = db.execute("SELECT root, status FROM batches").fetchall()
    assert len(rows) == 1 and rows[0]["status"] == "registered" and out["root"] == rows[0]["root"]
    assert chain.sent == 1
