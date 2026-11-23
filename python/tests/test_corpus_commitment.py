"""Corpus commitment: Merkle tree, committed artifacts, deterministic replay, counterexamples."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
D = ROOT / "webdemo" / "data"
sys.path.insert(0, str(ROOT / "python")); sys.path.insert(0, str(ROOT / "webdemo"))
import verify_citations as vc  # noqa: E402

sha = lambda b: "0x" + hashlib.sha256(b).hexdigest()
CFG_RAW = (D / "retrieval_config_v1.json").read_bytes()
CFG = json.loads(CFG_RAW)
ONCHAIN = json.loads((D / "corpus_onchain.json").read_text())
ROWS = [json.loads(l) for l in (D / "corpus_v1.jsonl").read_text(encoding="utf-8").splitlines()]


def test_config_and_artifacts_match_commitment():
    assert sha(CFG_RAW) == ONCHAIN["config_hash"]
    for a in (CFG["corpus"], CFG["retriever_code"], CFG["model"]["weights"], CFG["model"]["tokenizer"]):
        assert sha((D / a["path"]).read_bytes()) == a["sha256"], a["path"]


def test_corpus_file_is_sorted_canonical_and_hashes_match():
    assert len(ROWS) == ONCHAIN["leaf_count"] == 1998
    assert [r["hash"] for r in ROWS] == sorted(r["hash"] for r in ROWS)
    for r in ROWS:
        assert r["hash"] == sha(r["text"].encode("utf-8"))


def test_merkle_root_and_every_proof():
    lv = vc.merkle_levels([bytes.fromhex(r["hash"][2:]) for r in ROWS])
    root = "0x" + lv[-1][0].hex()
    assert root == ONCHAIN["root"] == CFG["corpus"]["merkle_root"]
    for i, r in enumerate(ROWS):
        assert vc.merkle_verify(r["hash"], i, len(ROWS), vc.merkle_proof(lv, i), root)


@pytest.mark.parametrize("n", [1, 2, 3, 5, 7, 8, 9, 33])
def test_merkle_small_trees_and_forgeries(n):
    hs = [hashlib.sha256(bytes([i])).digest() for i in range(n)]
    lv = vc.merkle_levels(hs); root = "0x" + lv[-1][0].hex()
    for i in range(n):
        p = vc.merkle_proof(lv, i)
        assert vc.merkle_verify("0x" + hs[i].hex(), i, n, p, root)
        if (i ^ 1) < n:  # same path, claimed at the sibling position: left/right swap changes the root
            assert not vc.merkle_verify("0x" + hs[i].hex(), i ^ 1, n, p, root)
        assert not vc.merkle_verify("0x" + hs[i].hex(), n, n, p, root)  # out of range
        assert not vc.merkle_verify("0x" + "00" * 32, i, n, p, root)
        if p:
            assert not vc.merkle_verify("0x" + hs[i].hex(), i, n, p[:-1], root)
    # an inner node must not pass as a leaf (domain separation)
    if n >= 2:
        assert not vc.merkle_verify("0x" + lv[1][0].hex(), 0, len(lv[1]), vc.merkle_proof(lv[1:], 0), root)


def test_replay_diff_reasons():
    a = [dict(id="0x" + "aa" * 32, rrf=0.03, bm25=1, dense=2), dict(id="0x" + "bb" * 32, rrf=0.02, bm25=None, dense=1)]
    assert vc.replay_diff(a, a) == ""
    assert "omits it" in vc.replay_diff(a[1:], a)
    assert "differs" in vc.replay_diff(a[::-1], a)
    assert "scores differ" in vc.replay_diff([dict(a[0], bm25=3), a[1]], a)


def test_reference_retriever_deterministic_and_matches_bundle():
    np = pytest.importorskip("numpy")  # noqa: F841
    import retrieval_v1 as R
    r = R.Retriever(ROWS, R.load_vocab(D / "potion-base-8M/tokenizer.json"), R.load_weights(D / "potion-base-8M/model.safetensors"), CFG)
    b = json.loads((D / "example_bundle.json").read_text())
    got = r.rank(b["retrieval"]["query"])
    assert got == r.rank(b["retrieval"]["query"])
    assert vc.replay_diff(json.loads(b["retrieval"]["ranked_json"]), got) == ""
    assert sha(b["retrieval"]["ranked_json"].encode()) == b["retrieval"]["ranked_hash"]


def test_counterexample_bundles_are_internally_consistent():
    """The dishonest-server bundles hash correctly; only inclusion / replay can expose them."""
    for name in ("hide", "inject"):
        b = json.loads((ROOT / f"experiments/demo/bundle_corpus_{name}.json").read_text())
        R = b["retrieval"]; sysmsg = json.loads(b["inference"]["request_body"])["messages"][0]["content"]
        assert f"retrieval={sha(R['ranked_json'].encode())}" in sysmsg
    inj = json.loads((ROOT / "experiments/demo/bundle_corpus_inject.json").read_text())["retrieval"]
    first = json.loads(inj["ranked_json"])[0]
    assert not vc.merkle_verify(first["id"], inj["proofs"][0]["leaf_index"], inj["leaf_count"], inj["proofs"][0]["path"], inj["corpus_root"])
