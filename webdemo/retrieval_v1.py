"""Reference retriever, version 1. Its sha256 is part of the retrieval config committed on Arc
(CorpusRegistry.configHash), so this exact file defines what "the committed retrieval" means.

Deterministic hybrid retrieval over the committed corpus (needs only numpy):
  BM25   lowercase, [a-z0-9]+ tokens minus stopwords; Okapi with idf = ln(1 + (N - df + .5)/(df + .5))
  dense  minishlab/potion-base-8M (model2vec static embeddings): BERT-uncased WordPiece tokens
         (first 512, [UNK] dropped), vector = sum of token rows, L2-normalised; similarity = dot
  fuse   reciprocal rank fusion of the BM25 top-50 and the dense top-50 (k = 60)
All arithmetic is float64. Orderings use scores quantised to 1e-9, ties broken by leaf index, so an
independent implementation (static/arcverify.js) reproduces the ranking exactly.
"""
from __future__ import annotations

import json
import math
import re
import struct
import unicodedata

import numpy as np

VERSION = "hybrid-v1"


# ------------------------------------------------------------------ artifacts
def load_corpus(path) -> list[dict]:
    """corpus_v1.jsonl: one canonical {"hash","text"} object per line, in leaf (sorted-hash) order."""
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_weights(path) -> np.ndarray:
    raw = open(path, "rb").read()
    n = struct.unpack("<Q", raw[:8])[0]
    meta = json.loads(raw[8:8 + n])["embeddings"]
    assert meta["dtype"] == "F32"
    a, b = meta["data_offsets"]
    return np.frombuffer(raw[8 + n + a: 8 + n + b], dtype="<f4").reshape(meta["shape"]).astype(np.float64)


def load_vocab(path) -> dict[str, int]:
    return json.load(open(path, encoding="utf-8"))["model"]["vocab"]


# ------------------------------------------------------------------ tokenizers
def bm25_tokens(s: str, stop: set[str]) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in stop]


def _cjk(cp: int) -> bool:
    return (0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF or 0x20000 <= cp <= 0x2A6DF or 0x2A700 <= cp <= 0x2B73F
            or 0x2B740 <= cp <= 0x2B81F or 0x2B820 <= cp <= 0x2CEAF or 0xF900 <= cp <= 0xFAFF or 0x2F800 <= cp <= 0x2FA1F)


def bert_words(text: str) -> list[str]:
    """BertNormalizer(clean_text, handle_chinese_chars, strip_accents, lowercase) + BertPreTokenizer."""
    out = []
    for ch in text:
        cp = ord(ch)
        if cp == 0 or cp == 0xFFFD or (ch not in "\t\n\r" and unicodedata.category(ch).startswith("C")):
            continue
        if ch.isspace():
            out.append(" ")
        elif _cjk(cp):
            out.append(" " + ch + " ")
        else:
            out.append(ch)
    s = "".join(c for c in unicodedata.normalize("NFD", "".join(out)) if unicodedata.category(c) != "Mn").lower()
    words, cur = [], []
    for ch in s:
        if ch.isspace():
            if cur:
                words.append("".join(cur)); cur = []
        elif (ch.isascii() and not ch.isalnum() and 33 <= ord(ch) <= 126) or unicodedata.category(ch).startswith("P"):
            if cur:
                words.append("".join(cur)); cur = []
            words.append(ch)
        else:
            cur.append(ch)
    if cur:
        words.append("".join(cur))
    return words


def wordpiece_ids(text: str, vocab: dict[str, int], max_tokens: int = 512) -> list[int]:
    unk = vocab["[UNK]"]
    ids: list[int] = []
    for w in bert_words(text):
        if len(w) > 100:
            ids.append(unk); continue
        start, pieces = 0, []
        while start < len(w):
            end, cur = len(w), None
            while start < end:
                sub = ("##" if start else "") + w[start:end]
                if sub in vocab:
                    cur = vocab[sub]; break
                end -= 1
            if cur is None:
                pieces = [unk]; break
            pieces.append(cur); start = end
        ids.extend(pieces)
    return [i for i in ids[:max_tokens] if i != unk]


# ------------------------------------------------------------------ retriever
def _q(x: float) -> int:
    return int(round(x * 1e9))


def _top(scores, n: int) -> list[int]:
    return sorted(range(len(scores)), key=lambda i: (-_q(scores[i]), i))[:n]


class Retriever:
    def __init__(self, docs: list[dict], vocab: dict[str, int], W: np.ndarray, cfg: dict) -> None:
        self.docs, self.vocab, self.W, self.cfg = docs, vocab, W, cfg
        self.stop = set(cfg["bm25"]["stopwords"])
        self.k1, self.b = float(cfg["bm25"]["k1"]), float(cfg["bm25"]["b"])
        self.max_tokens = int(cfg["dense"]["max_tokens"])
        toks = [bm25_tokens(d["text"], self.stop) for d in docs]
        self.tf = [{} for _ in docs]
        self.df: dict[str, int] = {}
        for i, t in enumerate(toks):
            for w in t:
                self.tf[i][w] = self.tf[i].get(w, 0) + 1
            for w in self.tf[i]:
                self.df[w] = self.df.get(w, 0) + 1
        self.dl = [len(t) for t in toks]
        self.N = len(docs)
        self.avgdl = sum(self.dl) / self.N
        self.E = np.stack([self.embed(d["text"]) for d in docs])

    def embed(self, text: str) -> np.ndarray:
        acc = np.zeros(self.W.shape[1], dtype=np.float64)
        for i in wordpiece_ids(text, self.vocab, self.max_tokens):
            acc += self.W[i]
        n = math.sqrt(sum(float(x) * float(x) for x in acc))
        return acc / n if n > 0 else acc

    def bm25(self, query: str) -> list[float]:
        q = bm25_tokens(query, self.stop)
        idf = {w: math.log(1 + (self.N - self.df[w] + 0.5) / (self.df[w] + 0.5)) for w in set(q) if w in self.df}
        out = []
        for i in range(self.N):
            s, tf, norm = 0.0, self.tf[i], self.k1 * (1 - self.b + self.b * self.dl[i] / self.avgdl)
            for w in q:
                f = tf.get(w, 0)
                if f:
                    s += idf[w] * f * (self.k1 + 1) / (f + norm)
            out.append(s)
        return out

    def rank(self, query: str) -> list[dict]:
        fu = self.cfg["fusion"]
        depth, pool, k = int(fu["list_depth"]), int(fu["pool"]), int(fu["k"])
        qv = self.embed(query)
        dense = [float(x) for x in self.E @ qv]
        bm, dn = _top(self.bm25(query), depth), _top(dense, depth)
        s: dict[int, float] = {}
        r_b = {d: r + 1 for r, d in enumerate(bm)}
        r_d = {d: r + 1 for r, d in enumerate(dn)}
        for d in bm:
            s[d] = 1.0 / (k + r_b[d])
        for d in dn:
            s[d] = s.get(d, 0.0) + 1.0 / (k + r_d[d])
        order = sorted(s, key=lambda d: (-s[d], d))[:pool]
        return [dict(id=self.docs[d]["hash"], rrf=s[d], bm25=r_b.get(d), dense=r_d.get(d)) for d in order]
