"""Offline tests for the standalone citation verifier helpers (no network)."""
import hashlib

from verify_citations import cid_v1_raw, dyn_string, hashes_in_calldata


def test_cid_matches_kubo_vector():
    d = hashlib.sha256(b"hello world").digest()
    assert cid_v1_raw(d) == "bafkreifzjut3te2nhyekklss27nh3k72ysco7y32koao5eei66wof36n5e"


def test_decode_register_batch_calldata():
    h1, h2 = "11" * 32, "22" * 32
    data = "0x28916b05" + f"{32:064x}" + f"{2:064x}" + h1 + h2
    fn, hs = hashes_in_calldata(data)
    assert fn == "registerBatch" and hs == ["0x" + h1, "0x" + h2]


def test_decode_register_source_calldata_and_unknown():
    h = "ab" * 32
    fn, hs = hashes_in_calldata("0x28f3c62c" + h + "00" * 64)
    assert fn == "registerSource" and hs == ["0x" + h]
    assert hashes_in_calldata("0xdeadbeef")[1] == []


def test_dyn_string():
    s = "bafy"
    blob = (32).to_bytes(32, "big") + len(s).to_bytes(32, "big") + s.encode().ljust(32, b"\0")
    assert dyn_string(blob, 32) == "bafy"


def test_canonical_and_passage_parsing():
    from verify_citations import PASSAGE_RE, canonical

    assert canonical({"b": 1, "a": "é"}) == '{"a":"é","b":1}'.encode("utf-8")
    user = "intro\n\n[P1] first passage\nwith newline\n\n[P2] second\n\nQuestion: q?"
    assert PASSAGE_RE.findall(user) == [("P1", "first passage\nwith newline"), ("P2", "second")]
