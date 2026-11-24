"""Offline tests: Merkle scheme (odd promotion, single leaf, side-bit proofs), JCS, URL normalization,
and that the standalone verifier implements the same scheme."""
import json
import random

import pytest

import verify_news as V
from newsreg import scheme as S


def test_single_leaf_root_is_leaf():
    l = S.para_leaf("only paragraph")
    assert S.root([l]) == l and S.proof(S.levels([l]), 0) == []


def test_odd_node_promoted_not_duplicated():
    a, b, c = (S.para_leaf(x) for x in "abc")
    assert S.root([a, b, c]) == S.node(S.node(a, b), c)
    assert S.root([a, b, c]) != S.node(S.node(a, b), S.node(c, c))


def test_empty_tree_rejected():
    with pytest.raises(ValueError):
        S.levels([])


@pytest.mark.parametrize("n", list(range(1, 18)) + [33, 100])
def test_every_proof_verifies_and_is_short(n):
    leaves = [S.para_leaf(f"p{i}") for i in range(n)]
    lv = S.levels(leaves)
    for i in range(n):
        p = S.proof(lv, i)
        assert len(p) <= S.max_proof_len(n)
        assert S.apply_proof(leaves[i], p) == lv[-1][0]
        assert V.apply_proof(leaves[i], p) == lv[-1][0]
    assert V.merkle_root(leaves) == lv[-1][0]


def test_proof_side_bits_matter():
    leaves = [S.para_leaf(f"p{i}") for i in range(4)]
    lv = S.levels(leaves)
    p = S.proof(lv, 1)
    assert p[0].startswith("L:")
    flipped = ["R:" + p[0][2:]] + p[1:]
    assert S.apply_proof(leaves[1], flipped) != lv[-1][0]


def test_domain_separation():
    # a paragraph whose text equals an internal node's preimage can't collide: leaves start 0x00, nodes 0x01, articles 0x02
    a, b = S.para_leaf("x"), S.para_leaf("y")
    assert S.para_leaf((b"\x01" + a + b).decode("latin-1")) != S.node(a, b)


def test_jcs_rfc8785_strings_and_order():
    # RFC 8785 sec. 3.2.2.2 string escaping; keys sorted by UTF-16 code units
    assert S.jcs({"b": 1, "a": [True, None, "x"]}) == b'{"a":[true,null,"x"],"b":1}'
    assert S.jcs("\u20ac$\u000f\nA'B\"\\\\\"/") == '"€$\\u000f\\nA\'B\\"\\\\\\\\\\"/"'.encode()
    assert S.jcs({"\u20ac": 1, "\U0001f600": 2, "\ufb33": 3}) == '{"\u20ac":1,"\U0001f600":2,"\ufb33":3}'.encode()  # UTF-16: U+1F600 (D83D..) sorts before U+FB33, unlike code-point order
    with pytest.raises(TypeError):
        S.jcs({"a": 1.5})
    m = {"z": "é", "a": None, "m": 3}
    assert S.jcs(m) == V.jcs(m)


def test_meta_keys_enforced():
    with pytest.raises(ValueError):
        S.meta_hash({"url": "x"})


def test_url_normalization():
    n = S.normalize_url
    assert n("https://WWW.BBC.co.uk:443/news/articles/x?at_medium=RSS&at_campaign=rss#top") == "https://www.bbc.co.uk/news/articles/x"
    assert n("https://e.com/a?id=7&utm_source=tw&fbclid=1&page=2") == "https://e.com/a?id=7&page=2"
    assert n("http://e.com:8080/a?b=1") == "http://e.com:8080/a?b=1"
    assert n("https://e.com") == "https://e.com/"


def test_scope_hash_order_independent():
    r = [S.hx(S.sha(bytes([i]))) for i in range(5)]
    assert S.scope_hash(r) == S.scope_hash(list(reversed(r))) == V.scope_hash(r)


def test_verifier_article_leaf_matches():
    meta = dict(url="https://e.com/a", outlet="E", title="T", published_at=None, fetched_at="2026-11-24T00:00:00Z",
                extractor_version="x", normalization="0x00", raw_html_sha256="0x11", version=1)
    ar = S.para_leaf("p")
    assert S.article_leaf(meta, ar) == V.article_leaf(meta, ar)


def test_selectors_match_contract():
    from eth_utils import keccak
    for sig, sel in V.vc.SEL.items():
        assert keccak(text=sig)[:4].hex() == sel, sig
    assert "0x" + keccak(text="BatchRegistered(bytes32,bytes32,address,uint32,uint256)").hex() == V.TOPIC_NEWS_BATCH
