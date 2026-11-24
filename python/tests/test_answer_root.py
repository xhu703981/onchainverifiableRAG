"""Single answer root: fixed labeled leaves, label binding, single-leaf proofs, every piece changes the root (offline)."""
import base64
import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))
import verify_citations as vc  # noqa: E402

B = json.loads((ROOT / "webdemo" / "data" / "example_bundle.json").read_text(encoding="utf-8"))


def test_bundle_leaves_recompute_to_the_recorded_root():
    mine = vc.answer_leaf_values(B)
    assert [k for k, _ in mine] == vc.ANSWER_LEAVES
    assert mine == [(x["label"], x["value"]) for x in B["answer_root"]["leaves"]]
    assert vc.answer_root_of(mine) == B["answer_root"]["root"]


def test_leaf_scheme_is_domain_separated_and_label_bound():
    x = vc.answer_leaf_x("model", "openai/gpt-4o-mini")
    assert x == hashlib.sha256(b"model\x00openai/gpt-4o-mini").digest()
    pairs = vc.answer_leaf_values(B)
    swapped = [("provider" if k == "served_model" else "served_model" if k == "provider" else k, v) for k, v in pairs]
    assert vc.answer_root_of(swapped) != vc.answer_root_of(pairs)  # same values under other labels -> other root
    assert vc.answer_root_of(pairs[::-1]) != vc.answer_root_of(pairs)  # order is fixed
    # single leaf: no ambiguity between a leaf and an inner node (0x00 vs 0x01 prefix)
    assert vc.merkle_leaf(x) != vc.merkle_node(x[:16] * 2, x[16:] * 2)


@pytest.mark.parametrize("label", vc.ANSWER_LEAVES)
def test_single_leaf_proof_reaches_the_root(label):
    pairs = vc.answer_leaf_values(B)
    value = dict(pairs)[label]
    path = vc.answer_leaf_proof(pairs, label)
    x = "0x" + vc.answer_leaf_x(label, value).hex()
    assert vc.merkle_verify(x, vc.ANSWER_LEAVES.index(label), len(pairs), path, B["answer_root"]["root"])
    assert not vc.merkle_verify("0x" + vc.answer_leaf_x(label, value + "x").hex(), vc.ANSWER_LEAVES.index(label),
                                len(pairs), path, B["answer_root"]["root"])


def _resp(b, f):
    raw = json.loads(base64.b64decode(b["inference"]["response_body_b64"]))
    f(raw)
    b["inference"]["response_body_b64"] = base64.b64encode(json.dumps(raw).encode()).decode()


EDITS = {
    "answer": lambda b: b.update(answer=b["answer"] + "!"),
    "cited text": lambda b: b["citations"][0].update(text=b["citations"][0]["text"].replace("Hux", "Hax", 1)),
    "cited id": lambda b: b["citations"][0].update(id="P9"),
    "question": lambda b: b.update(question=b["question"] + "?"),
    "prompt": lambda b: b["inference"].update(request_body=b["inference"]["request_body"].replace("[P1] ", "[P1] Note: ")),
    "served model": lambda b: _resp(b, lambda r: r.update(model="meta-llama/llama-3.1-8b-instruct")),
    "provider": lambda b: _resp(b, lambda r: r.update(provider="Someone")),
    "requested model": lambda b: b["inference"].update(model="openai/gpt-4o"),
    "ranking": lambda b: b["retrieval"].update(ranked_json=json.dumps(json.loads(b["retrieval"]["ranked_json"])[1:])),
    "corpus root": lambda b: b["retrieval"].update(corpus_root="0x" + "11" * 32),
    "config": lambda b: b["retrieval"].update(config_hash="0x" + "22" * 32),
    "anchor block": lambda b: b["inference"]["anchor_block"].update(number=b["inference"]["anchor_block"]["number"] - 1),
    "a step time": lambda b: b["inference"].update(client_response_received_ts="2026-11-24T00:00:00.000000Z"),
}


@pytest.mark.parametrize("what", list(EDITS))
def test_changing_any_piece_changes_the_root(what):
    b = copy.deepcopy(B)
    EDITS[what](b)
    assert vc.answer_root_of(vc.answer_leaf_values(b)) != B["answer_root"]["root"], what


def test_iso_ms_parses_microseconds_to_ms():
    assert vc.iso_ms("1970-01-01T00:00:01.123456Z") == 1123
    assert vc.iso_ms("1970-01-01T00:00:01.5Z") == 1500
    with pytest.raises(ValueError):
        vc.iso_ms("2026-11-23 10:00:00")


def test_old_inferencelog_bundles_take_the_old_path():
    old = json.loads((ROOT / "webdemo" / "data" / "example_bundle_inferencelog.json").read_text(encoding="utf-8"))
    assert "answer_root" not in old and old["inference"]["log_contract"]
