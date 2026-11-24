"""Acceptance: genuine bundle PASSES; four tampered bundles FAIL for the right reason. Reads Arc testnet."""
import copy
import json
import os
from pathlib import Path

import pytest

import verify_citations as vc
import verify_news as V
from newsreg import scheme as S

EX = Path(__file__).resolve().parents[1] / "examples"
pytestmark = pytest.mark.skipif(os.environ.get("NEWS_OFFLINE") == "1", reason="needs Arc testnet RPC")


@pytest.fixture(scope="module")
def rpc():
    return vc.RPC(vc.DEFAULT_RPC)


@pytest.fixture(scope="module")
def genuine():
    return json.loads((EX / "bundle_genuine.json").read_text())


def run(rpc, b):
    res = V.verify_news(rpc, b)
    inf = vc.verify_inference(rpc, b)
    failed = {n for c in res["citations"] for n, o, _ in c["checks"] if o is False} | {n for n, o, _ in res["scope"] if o is False} \
        | {n for n, o, _ in inf if not o}
    return V.all_ok(res, inf), failed


def test_genuine_passes(rpc, genuine):
    ok, failed = run(rpc, genuine)
    assert ok, failed


def test_edited_paragraph_fails(rpc, genuine):
    b = copy.deepcopy(genuine)
    b["citations"][0]["text"] = b["citations"][0]["text"].replace("8%", "18%")
    ok, failed = run(rpc, b)
    assert not ok
    assert any(n.startswith("N1 sha256") for n in failed)
    assert any(n.startswith("M3 cited") for n in failed)


def test_swapped_url_meta_fails(rpc, genuine):
    b = copy.deepcopy(genuine)
    b["citations"][0]["article"]["meta"]["url"] = "https://www.reuters.com/markets/anthropic-stock"
    ok, failed = run(rpc, b)
    assert not ok and any(n.startswith("N2 article leaf") for n in failed)
    b = copy.deepcopy(genuine)
    b["citations"][0]["article"]["meta"]["published_at"] = "2026-11-20T00:00:00Z"
    ok, failed = run(rpc, b)
    assert not ok and any(n.startswith("N2 article leaf") for n in failed)


def test_proof_to_unregistered_root_fails(rpc, genuine):
    b = copy.deepcopy(genuine)
    c = b["citations"][0]
    leaf = S.article_leaf(c["article"]["meta"], S.unhx(c["article"]["article_root"]))
    sib = S.sha(b"some other article")
    c["article"]["proof"] = ["R:" + S.hx(sib)]
    c["batch"]["root"] = S.hx(S.node(leaf, sib))  # self-consistent proof, but this root was never registered
    ok, failed = run(rpc, b)
    assert not ok and "N3 batch root is registered on NewsRegistry" in failed


def test_root_registered_by_untrusted_key_fails(rpc, genuine):
    ub = json.loads((EX / "untrusted_batch.json").read_text())
    b = copy.deepcopy(genuine)
    c = b["citations"][0]
    a = next(x for x in ub["articles"] if x["article_root"] == c["article"]["article_root"])
    c["article"]["proof"], c["article"]["index"] = a["proof"], a["index"]
    c["batch"].update(root=ub["root"], manifest_hash=ub["manifest_hash"], tx=ub["tx"], block=ub["block"], block_time=ub["block_time"])
    res = V.verify_news(rpc, b, manifests=False)
    checks = {n: o for n, o, _ in res["citations"][0]["checks"]}
    assert checks["N2 article leaf (JCS meta + article_root) -> proof -> batch_root"] is True  # proof is genuine...
    assert checks["N3 batch root is registered on NewsRegistry"] is True                     # ...and really registered...
    assert checks["N4 registration tx is the real registerBatch for this root, from the registrant, at the on-chain time"] is True
    assert checks["N3 isTrustedRoot (registered AND registrant on the CURRENT allowlist)"] is False  # ...but not by a trusted key
    assert not V.all_ok(res, [])
    # and if YOU choose to trust that key, it passes the trust check: trust is the verifier's choice
    res2 = V.verify_news(rpc, b, trust={ub["registrant"].lower()}, manifests=False)
    assert {n: o for n, o, _ in res2["citations"][0]["checks"]}["N3 registrant is in YOUR trusted list (--trust)"] is True


def test_outlet_choice(rpc, genuine):
    res = V.verify_news(rpc, genuine, outlets={"BBC News"}, manifests=False)
    assert {n: o for n, o, _ in res["citations"][0]["checks"]}["U1 cited outlet is one of YOUR chosen sources (--outlets)"] is False
