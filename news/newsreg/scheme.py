"""news-merkle-v1: canonical JSON, URL normalisation, and the Merkle scheme. Standard library only,
so the verifier can import the same definitions. Versioned separately from CorpusRegistry's scheme.

  paragraph leaf = sha256(0x00 || utf8(paragraph_text))
  internal node  = sha256(0x01 || left || right); an odd node at the end of a level is promoted unchanged
  single leaf    : root = leaf.   (articles with 0 paragraphs are never batched)
  article_root   = Merkle over its paragraph leaves, in order
  article leaf   = sha256(0x02 || sha256(JCS(meta)) || article_root)
  batch_root     = Merkle over article leaves, in manifest order
  proof          = list of "L:0x.." / "R:0x.." = sibling on the Left / Right, bottom-up; promoted levels add no step
"""
from __future__ import annotations

import hashlib
import json
import math
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SCHEME = "news-merkle-v1"
META_VERSION = 1
META_KEYS = ("extractor_version", "fetched_at", "normalization", "outlet", "published_at", "raw_html_sha256", "title", "url", "version")


def sha(b: bytes) -> bytes:
    return hashlib.sha256(b).digest()


def hx(b: bytes) -> str:
    return "0x" + b.hex()


def unhx(s: str) -> bytes:
    return bytes.fromhex(s[2:] if s.startswith("0x") else s)


# ------------------------------------------------------------------ RFC 8785 (JCS)
def jcs(obj) -> bytes:
    """RFC 8785 JSON Canonicalization Scheme for the value types we use (str, int, bool, None, list, dict).
    Keys sorted by UTF-16 code units; no whitespace; strings escaped exactly like ECMAScript JSON.stringify
    (Python's json with ensure_ascii=False matches it: \\b\\f\\n\\r\\t\\"\\\\, other C0 controls as \\u00xx lowercase).
    Integral floats print as integers (as ECMAScript does); other floats are rejected."""
    def enc(v) -> str:
        if v is None or isinstance(v, bool):
            return json.dumps(v)
        if isinstance(v, float) and v.is_integer():  # ECMAScript prints 1.0 as "1"
            v = int(v)
        if isinstance(v, int):
            if abs(v) > 2**53 - 1:
                raise ValueError("integer outside the IEEE-754 safe range")
            return str(v)
        if isinstance(v, str):
            v.encode("utf-8")  # rejects lone surrogates
            return json.dumps(v, ensure_ascii=False)
        if isinstance(v, (list, tuple)):
            return "[" + ",".join(enc(x) for x in v) + "]"
        if isinstance(v, dict):
            ks = sorted(v, key=lambda k: k.encode("utf-16-be"))
            return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + enc(v[k]) for k in ks) + "}"
        raise TypeError(f"JCS: unsupported type {type(v).__name__}")
    return enc(obj).encode("utf-8")


# ------------------------------------------------------------------ URLs
TRACKING_PREFIXES = ("utm_", "at_", "mc_", "pk_", "hsa_", "itm_", "ga_")
TRACKING_KEYS = {"fbclid", "gclid", "dclid", "msclkid", "yclid", "igshid", "cmpid", "cmp", "ocid", "ito", "ref", "ref_src",
                 "src", "source", "rss", "taid", "smid", "partner", "mbid", "guccounter", "share", "traffic_source",
                 "intcmp", "int_cmp", "cid", "feature", "sr_share", "ns_campaign", "ns_mchannel", "ns_source", "ns_linkname"}


def normalize_url(url: str) -> str:
    """Lowercase scheme+host, drop default port, fragment and tracking parameters (utm_*, at_*, fbclid, ...).
    Remaining query parameters keep their order."""
    p = urlsplit(url.strip())
    scheme = (p.scheme or "https").lower()
    host = (p.hostname or "").lower()
    if p.port and not ((scheme == "http" and p.port == 80) or (scheme == "https" and p.port == 443)):
        host += f":{p.port}"
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
         if not k.lower().startswith(TRACKING_PREFIXES) and k.lower() not in TRACKING_KEYS]
    return urlunsplit((scheme, host, p.path or "/", urlencode(q), ""))


# ------------------------------------------------------------------ Merkle
def para_leaf(text: str) -> bytes:
    return sha(b"\x00" + text.encode("utf-8"))


def node(l: bytes, r: bytes) -> bytes:
    return sha(b"\x01" + l + r)


def meta_hash(meta: dict) -> bytes:
    if tuple(sorted(meta)) != META_KEYS:
        raise ValueError(f"meta keys must be exactly {META_KEYS}")
    return sha(jcs(meta))


def article_leaf(meta: dict, article_root: bytes) -> bytes:
    return sha(b"\x02" + meta_hash(meta) + article_root)


def levels(leaves: list[bytes]) -> list[list[bytes]]:
    if not leaves:
        raise ValueError("empty tree (0-leaf articles/batches are not allowed)")
    lv = [list(leaves)]
    while len(lv[-1]) > 1:
        cur = lv[-1]
        nxt = [node(cur[i], cur[i + 1]) for i in range(0, len(cur) - 1, 2)]
        if len(cur) % 2:
            nxt.append(cur[-1])  # odd node promoted unchanged
        lv.append(nxt)
    return lv


def root(leaves: list[bytes]) -> bytes:
    return levels(leaves)[-1][0]


def proof(lv: list[list[bytes]], index: int) -> list[str]:
    out, i = [], index
    for cur in lv[:-1]:
        sib = i ^ 1
        if sib < len(cur):
            out.append(("L:" if sib < i else "R:") + hx(cur[sib]))
        i //= 2
    return out


def apply_proof(leaf: bytes, path: list[str]) -> bytes:
    h = leaf
    for step in path:
        side, s = step.split(":", 1)
        sib = unhx(s)
        if len(sib) != 32 or side not in ("L", "R"):
            raise ValueError("malformed proof step")
        h = node(sib, h) if side == "L" else node(h, sib)
    return h


def scope_hash(batch_roots: list[str]) -> str:
    """Retrieval scope bound into the committed prompt: sha256 over the sorted, de-duplicated batch roots."""
    rs = sorted({r.lower() for r in batch_roots})
    return hx(sha(b"".join(unhx(r) for r in rs)))


def max_proof_len(n: int) -> int:
    return math.ceil(math.log2(n)) if n > 1 else 0
