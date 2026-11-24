"""News RAG: answers ONLY from paragraphs in trusted registered batches, logs the call in InferenceLog
(commit before, finalize after, unchanged contract), and returns a bundle anyone can verify."""
from __future__ import annotations

import base64
import calendar
import datetime as dt
import hashlib
import json
import pickle
import re
import secrets
import sys
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np
from eth_abi import encode

from . import scheme as S
from .chain import EXPLORER, RPC, Signer, call, deployment, is_trusted_roots, load_env, selector, webdemo_dir
from .db import DATA, connect

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(webdemo_dir()))
import retrieval_v1  # noqa: E402  (the committed reference retriever, reused unchanged)

CONFIG_PATH = REPO / "news" / "retrieval_news_v1.json"
INFERENCE_LOG = "0xc7CfFb4Ae006AccC82813829CCC466B964aBCAE3"
MODEL = "openai/gpt-4o-mini"
NOT_FOUND = "Not found in registered news."
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august", "september",
                                      "october", "november", "december"], 1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
MONTHS["sept"] = 9


def canonical(obj) -> bytes:  # same serialization as the existing demo (M2 checks it)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def iso(ts: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def post_cutoff(question: str, as_of_ts: int) -> str | None:
    """Deterministic check for questions about events after the corpus cutoff."""
    q = question.lower()
    d = dt.datetime.fromtimestamp(as_of_ts, dt.timezone.utc).date()
    m = re.search(r"\b(tomorrow|next (?:week|month|year)|later this (?:week|month|year))\b", q)
    if m:
        return f"'{m.group(1)}' is after the corpus cutoff"
    mon = "|".join(sorted(MONTHS, key=len, reverse=True))
    for m in re.finditer(rf"\b({mon})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+((?:19|20)\d\d))?", q):
        y = int(m.group(3)) if m.group(3) else d.year
        try:
            when = dt.date(y, MONTHS[m.group(1)], int(m.group(2)))
        except ValueError:
            continue
        if when > d:
            return f"{when.isoformat()} is after the corpus cutoff {d.isoformat()}"
    for m in re.finditer(rf"\b({mon})\.?,?\s+((?:19|20)\d\d)\b", q):
        if (int(m.group(2)), MONTHS[m.group(1)]) > (d.year, d.month):
            return f"{m.group(1).title()} {m.group(2)} is after the corpus cutoff {d.isoformat()}"
    for m in re.finditer(r"\b((?:19|20)\d\d)\b", q):
        if int(m.group(1)) > d.year:
            return f"{m.group(1)} is after the corpus cutoff {d.isoformat()}"
    return None


class Index:
    """Paragraph index over the trusted scope (rebuilt when the scope changes; embeddings cached on disk)."""

    def __init__(self, docs: list[dict], cfg: dict, vocab, W, emb_cache: dict) -> None:
        self.docs = docs
        r = retrieval_v1.Retriever.__new__(retrieval_v1.Retriever)
        r.docs, r.vocab, r.W, r.cfg = docs, vocab, W, cfg
        r.stop = set(cfg["bm25"]["stopwords"])
        r.k1, r.b = float(cfg["bm25"]["k1"]), float(cfg["bm25"]["b"])
        r.max_tokens = int(cfg["dense"]["max_tokens"])
        toks = [retrieval_v1.bm25_tokens(d["text"], r.stop) for d in docs]
        r.tf = [{} for _ in docs]
        r.df = {}
        for i, t in enumerate(toks):
            for w in t:
                r.tf[i][w] = r.tf[i].get(w, 0) + 1
            for w in r.tf[i]:
                r.df[w] = r.df.get(w, 0) + 1
        r.dl = [len(t) for t in toks]
        r.N = len(docs)
        r.avgdl = sum(r.dl) / max(r.N, 1)
        rows = []
        for d in docs:
            e = emb_cache.get(d["hash"])
            if e is None:
                e = emb_cache[d["hash"]] = r.embed(d["text"])
            rows.append(e)
        r.E = np.stack(rows) if rows else np.zeros((0, W.shape[1]))
        self.r = r

    def search(self, q: str) -> list[dict]:
        ranked = self.r.rank(q)
        qv = self.r.embed(q)
        qt = set(retrieval_v1.bm25_tokens(q, self.r.stop))
        pos = {d["hash"]: i for i, d in enumerate(self.docs)}
        for x in ranked:
            i = pos[x["id"]]
            x["cosine"] = round(float(self.r.E[i] @ qv), 6)
            x["coverage"] = round(len(qt & set(self.r.tf[i])) / len(qt), 6) if qt else 0.0
        return ranked


class NewsEngine:
    def __init__(self, signer: Signer | None = None) -> None:
        self.env = load_env()
        self.rpc = RPC()
        self.rpc.check_chain()
        self.dep = deployment()
        self.registry = self.dep["news_registry"]
        self.signer = signer
        self.or_key = self.env.get("OPENROUTER_API_KEY", "")
        self.config_raw = CONFIG_PATH.read_bytes()
        self.config_hash = S.hx(S.sha(self.config_raw))
        self.cfg = json.loads(self.config_raw)
        md = webdemo_dir() / "data" / "potion-base-8M"
        self.vocab = retrieval_v1.load_vocab(md / "tokenizer.json")
        self.W = retrieval_v1.load_weights(md / "model.safetensors")
        self.cache_path = DATA / "emb_cache.pkl"
        self.emb = pickle.loads(self.cache_path.read_bytes()) if self.cache_path.exists() else {}
        self.index: Index | None = None
        self.index_scope = None
        self.lock = threading.Lock()

    # ------------------------------------------------------------ scope
    def scope(self) -> dict:
        db = connect()
        rows = db.execute("SELECT * FROM batches WHERE status='registered' ORDER BY id").fetchall()
        ok = is_trusted_roots(self.rpc, self.registry, [S.unhx(r["root"]) for r in rows])
        trusted = [dict(r) for r, o in zip(rows, ok) if o]
        if not trusted:
            raise RuntimeError("no trusted registered batch yet")
        roots = sorted(r["root"].lower() for r in trusted)
        return dict(batches=trusted, roots=roots, scope_hash=S.scope_hash(roots), as_of_ts=max(r["block_time"] for r in trusted))

    def ensure_index(self, sc: dict) -> None:
        if self.index_scope == sc["scope_hash"]:
            return
        db = connect()
        ids = [b["id"] for b in sorted(sc["batches"], key=lambda b: b["id"])]
        q = ("SELECT p.text, p.leaf, p.idx, p.proof, a.id AS aid FROM paragraphs p JOIN articles a ON a.id=p.article_id "
             f"WHERE a.batch_id IN ({','.join('?' * len(ids))}) ORDER BY a.batch_id, a.batch_index, p.idx")
        docs, seen = [], set()
        for r in db.execute(q, ids):
            h = S.hx(S.sha(r["text"].encode("utf-8")))
            if h in seen:
                continue
            seen.add(h)
            docs.append(dict(hash=h, text=r["text"], aid=r["aid"], idx=r["idx"], leaf=r["leaf"], proof=r["proof"]))
        n0 = len(self.emb)
        self.index = Index(docs, self.cfg, self.vocab, self.W, self.emb)
        if len(self.emb) != n0:
            self.cache_path.write_bytes(pickle.dumps(self.emb))
        self.index_scope = sc["scope_hash"]

    def relevant(self, x: dict) -> bool:
        rv = self.cfg["relevance"]
        return x["coverage"] >= rv["min_coverage"] and x["cosine"] >= rv["min_cosine"]

    # ------------------------------------------------------------ evidence for one paragraph
    def evidence(self, doc: dict, pid: str, batches: dict) -> dict:
        db = connect()
        a = db.execute("SELECT * FROM articles WHERE id=?", (doc["aid"],)).fetchone()
        b = batches[a["batch_id"]]
        return dict(id=pid, text=doc["text"], content_hash=doc["hash"],
                    paragraph=dict(leaf=doc["leaf"], proof=json.loads(doc["proof"])),
                    article=dict(meta=json.loads(a["meta_json"]), article_root=a["article_root"],
                                 leaf=a["article_leaf"], proof=json.loads(a["batch_proof"])),
                    batch=dict(root=b["root"], manifest_hash=b["manifest_hash"], manifest_uri=b["manifest_uri"],
                               article_count=b["article_count"], tx=b["tx"], block=b["block_number"], block_time=b["block_time"],
                               explorer=f"{EXPLORER}/tx/{b['tx']}"))

    def _send(self, sig: str, types: list[str], args: list) -> dict:
        r = self.signer.send(INFERENCE_LOG, selector(sig) + encode(types, args))
        return dict(tx=r["tx"], block=r["block"], usdc=r["cost_usdc"])

    # ------------------------------------------------------------ answer
    def ask(self, question: str, _attack: str | None = None) -> dict:
        question = " ".join(question.split())[:500]
        with self.lock:
            sc = self.scope()
            as_of = iso(sc["as_of_ts"])
            base = dict(kind="news", question=question, as_of=as_of,
                        as_of_statement=f"As of {as_of} (block time of the latest registered batch; proven not-after bound)",
                        chain_id=5042002, rpc=self.rpc.url,
                        news=dict(registry=self.registry, scheme=S.SCHEME, trust_level="L0",
                                  scope=dict(roots=sc["roots"], scope_hash=sc["scope_hash"]),
                                  config_hash=self.config_hash, config_json=self.config_raw.decode("utf-8")))
            why = post_cutoff(question, sc["as_of_ts"])
            if why:
                return dict(bundle=dict(base, answer=NOT_FOUND, abstain=dict(reason="post_cutoff", detail=why), citations=[]), passages=[])
            self.ensure_index(sc)
            ranked = self.index.search(question)
            rel = [x for x in ranked if self.relevant(x)]
            if not rel:
                top = ranked[0] if ranked else {}
                return dict(bundle=dict(base, answer=NOT_FOUND, citations=[],
                                        abstain=dict(reason="no_relevant_paragraph",
                                                     detail=f"best candidate coverage {top.get('coverage', 0):.2f}, cosine {top.get('cosine', 0):.2f}; needs coverage >= "
                                                            f"{self.cfg['relevance']['min_coverage']} and cosine >= {self.cfg['relevance']['min_cosine']}")),
                            passages=[])
            if not self.signer or not self.or_key:
                raise RuntimeError("missing OPENROUTER_API_KEY or ARC key")
            docs = {d["hash"]: d for d in self.index.docs}
            kept = [docs[x["id"]] for x in rel[: self.cfg["selection"]["prompt_k"]]]
            passages = [dict(id=f"P{n + 1}", text=d["text"], content_hash=d["hash"]) for n, d in enumerate(kept)]
            proot = S.sha(b"".join(S.unhx(p["content_hash"]) for p in passages))
            qkey = "news:" + hashlib.sha256(question.encode()).hexdigest()[:16]
            qid = S.sha(qkey.encode())
            cts = now_iso()
            aid = S.sha(qid + cts.encode() + secrets.token_bytes(16))
            meta = (f"Request metadata (audit only, do not mention): answer_id={S.hx(aid)}; question_id={S.hx(qid)}; client_ts={cts}; "
                    f"news_registry={self.registry}; scheme={S.SCHEME}; news_scope={sc['scope_hash']}; "
                    f"retrieval_config={self.config_hash}; as_of={as_of}")
            user = (f"The numbered passages are paragraphs from news articles registered up to {as_of}. "
                    "Answer the question using ONLY these passages.\n"
                    "Write 1-4 short sentences. End EVERY sentence with the ids of the passages that support it, placed before the period. "
                    "Never write a sentence without such a citation.\n"
                    'Reply with JSON only, for example {"answer": "X happened on Monday [P2]. Officials said Y [P1][P3].", "citations": ["P2", "P1", "P3"]} '
                    "(citations = every id you used, in first-use order).\n"
                    f'If the passages do not answer the question, or it asks about events after {as_of}, reply exactly '
                    f'{{"answer": "{NOT_FOUND}", "citations": []}}.\n\n'
                    + "\n\n".join(f"[{p['id']}] {p['text']}" for p in passages) + f"\n\nQuestion: {question}")
            body = {"model": MODEL, "temperature": 0, "max_tokens": 300,
                    "messages": [{"role": "system", "content": meta}, {"role": "user", "content": user}]}
            req = canonical(body)
            c = self._send("commit(bytes32,bytes32,bytes32,bytes32,bytes32,uint16)",
                           ["bytes32", "bytes32", "bytes32", "bytes32", "bytes32", "uint16"],
                           [aid, qid, S.sha(req), proot, S.sha(MODEL.encode()), len(passages)])
            r = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", req,
                                       {"Authorization": "Bearer " + self.or_key, "Content-Type": "application/json"})
            with urllib.request.urlopen(r, timeout=120) as resp:
                raw = resp.read()
            rts = now_iso()
            while int(self.rpc("eth_blockNumber", []), 16) <= c["block"]:
                time.sleep(0.3)
            f = self._send("finalize(bytes32,bytes32)", ["bytes32", "bytes32"], [aid, S.sha(raw)])
        content = json.loads(raw)["choices"][0]["message"]["content"]
        try:
            mj = json.loads(re.search(r"\{.*\}", content, re.S).group(0))
        except Exception:  # noqa: BLE001
            mj = {"answer": content, "citations": []}
        batches = {b["id"]: b for b in sc["batches"]}
        byid = {p["id"]: d for p, d in zip(passages, kept)}
        cites = [self.evidence(byid[pid], pid, batches) for pid in mj.get("citations", []) if pid in byid]
        jr = json.loads(raw)
        bundle = dict(base, answer=mj.get("answer"), citations=cites,
                      inference=dict(log_contract=INFERENCE_LOG, answer_id=S.hx(aid), question_key=qkey, question_id=S.hx(qid), model=MODEL,
                                     client_request_ts=cts, client_response_received_ts=rts,
                                     serialization='json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")',
                                     request_body=req.decode("utf-8"), request_hash=S.hx(S.sha(req)),
                                     passages=[dict(id=p["id"], content_hash=p["content_hash"]) for p in passages], passages_root=S.hx(proot),
                                     response_body_b64=base64.b64encode(raw).decode(), response_hash=S.hx(S.sha(raw)),
                                     provider_generation_id=jr.get("id"), provider_model=jr.get("model"), provider=jr.get("provider"),
                                     commit_tx=c["tx"], commit_block=c["block"], finalize_tx=f["tx"], finalize_block=f["block"],
                                     explorer=dict(commit=f"{EXPLORER}/tx/{c['tx']}", finalize=f"{EXPLORER}/tx/{f['tx']}")),
                      how_to_verify="python webdemo/verify_citations.py <this file>")
        return dict(bundle=bundle, passages=passages, stats=dict(gas_usdc=round(c["usdc"] + f["usdc"], 6), relevant=len(rel), pool=len(ranked)))
