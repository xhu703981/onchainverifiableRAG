"""Hourly fetch: RSS -> new URLs -> GET HTML -> save raw -> extract -> paragraphs + Merkle. Usage: python -m newsreg.crawl"""
from __future__ import annotations

import argparse
import calendar
import json
import time
from pathlib import Path

import feedparser

from . import scheme as S
from .db import DATA, connect
from .extract import EXTRACTOR_VERSION, NORMALIZATION_SPEC, extract_paragraphs, is_substantial, norm_text
from .fetcher import Fetcher, HttpFetcher, utc_now

SOURCES = Path(__file__).resolve().parent.parent / "sources.json"
PER_FEED = 12


def feed_time(e) -> str | None:
    t = e.get("published_parsed") or e.get("updated_parsed")
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(calendar.timegm(t))) if t else None


def build_article(raw: bytes, *, url: str, outlet: str, title: str, published_at: str | None, fetched_at: str) -> dict:
    """Pure function of the saved raw bytes + feed/crawler claims (used again by the reproducibility test)."""
    paras = extract_paragraphs(raw)
    meta = dict(url=url, outlet=outlet, title=norm_text(title or ""), published_at=published_at, fetched_at=fetched_at,
                extractor_version=EXTRACTOR_VERSION, normalization=NORMALIZATION_SPEC,
                raw_html_sha256=S.hx(S.sha(raw)), version=S.META_VERSION)
    out = dict(meta=meta, paragraphs=paras)
    if paras:
        leaves = [S.para_leaf(p) for p in paras]
        lv = S.levels(leaves)
        out.update(leaves=[S.hx(x) for x in leaves], proofs=[S.proof(lv, i) for i in range(len(leaves))],
                   article_root=S.hx(lv[-1][0]), article_leaf=S.hx(S.article_leaf(meta, lv[-1][0])),
                   meta_hash=S.hx(S.meta_hash(meta)))
    return out


def save_raw(raw: bytes) -> str:
    h = S.sha(raw).hex()
    p = DATA / "raw" / h[:2] / f"{h}.html"
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_bytes(raw)
    return str(p.relative_to(DATA))


def crawl(fetcher: Fetcher, per_feed: int = PER_FEED, only: str | None = None) -> None:
    db = connect()
    sources = json.loads(SOURCES.read_text())["sources"]
    for src in sources:
        if only and src["name"] != only:
            continue
        started, n_new, n_ok, err, entries = utc_now(), 0, 0, None, []
        try:
            entries = feedparser.parse(fetcher.fetch(src["rss_url"]).body).entries
        except Exception as e:  # noqa: BLE001
            err = f"feed: {type(e).__name__}: {e}"[:300]
        for e in entries[:40]:
            if n_new >= per_feed:
                break
            link = e.get("link")
            if not link:
                continue
            url = S.normalize_url(link)
            prev = db.execute("SELECT status FROM articles WHERE url=?", (url,)).fetchone()
            if prev and prev["status"] != "error":
                continue
            if prev:  # transient fetch error last time: retry
                db.execute("DELETE FROM articles WHERE url=? AND status='error'", (url,))
                db.commit()
            n_new += 1
            try:
                r = fetcher.fetch(link)
                raw = r.body
                save_raw(raw)
                a = build_article(raw, url=url, outlet=src["name"], title=e.get("title", ""), published_at=feed_time(e), fetched_at=r.fetched_at)
                status = "ok"
                if not a["paragraphs"]:
                    status = "empty"
                elif not is_substantial(a["paragraphs"]):
                    status = "short"
                elif db.execute("SELECT 1 FROM articles WHERE article_root=? AND status='ok'", (a["article_root"],)).fetchone():
                    status = "duplicate"
                m = a["meta"]
                cur = db.execute(
                    "INSERT INTO articles(url,url_raw,final_url,outlet,title,published_at,fetched_at,raw_sha256,extractor_version,normalization,"
                    "meta_json,meta_hash,article_root,article_leaf,n_paragraphs,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (url, link, r.final_url, m["outlet"], m["title"], m["published_at"], m["fetched_at"], m["raw_html_sha256"],
                     m["extractor_version"], m["normalization"], S.jcs(m).decode(), a.get("meta_hash"), a.get("article_root"),
                     a.get("article_leaf"), len(a["paragraphs"]), status))
                if status == "ok":
                    db.executemany("INSERT INTO paragraphs(article_id,idx,text,leaf,proof) VALUES(?,?,?,?,?)",
                                   [(cur.lastrowid, i, p, a["leaves"][i], json.dumps(a["proofs"][i])) for i, p in enumerate(a["paragraphs"])])
                    n_ok += 1
                db.commit()
            except Exception as ex:  # noqa: BLE001
                db.rollback()
                db.execute("INSERT OR IGNORE INTO articles(url,url_raw,outlet,title,fetched_at,raw_sha256,status,error) VALUES(?,?,?,?,?,?,?,?)",
                           (url, link, src["name"], e.get("title", ""), utc_now(), "", "error", f"{type(ex).__name__}: {ex}"[:300]))
                db.commit()
            time.sleep(1.0)  # politeness
        db.execute("INSERT INTO feed_runs(outlet,started_at,entries,new,ok,error) VALUES(?,?,?,?,?,?)",
                   (src["name"], started, len(entries), n_new, n_ok, err))
        db.commit()
        print(f"{utc_now()} {src['name']}: {len(entries)} entries, {n_new} new, {n_ok} ok{' ERR ' + err if err else ''}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-feed", type=int, default=PER_FEED)
    ap.add_argument("--only")
    a = ap.parse_args()
    crawl(HttpFetcher(), a.per_feed, a.only)
