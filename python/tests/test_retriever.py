"""Retriever filter tests with mocked registries (no network)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hashing import sha256_text, to_bytes32_hex
from retriever import VerifiedRetriever, chunk_content_hash, chunk_text


def _hash(text: str) -> str:
    return to_bytes32_hex(sha256_text(text))


class FakeRegistry:
    """In-memory model of SourceRegistry trust semantics."""

    def __init__(self, records: dict[str, str], trusted: set[str]) -> None:
        self.records = records  # hash -> registrant
        self.trusted = trusted
        self.batch_calls = 0

    def isRegistered(self, h):  # noqa: N802
        return h in self.records

    def isVerified(self, h):  # noqa: N802
        return self.records.get(h) in self.trusted

    def areVerified(self, hs):  # noqa: N802
        self.batch_calls += 1
        return [self.isVerified(h) for h in hs]

    def areRegistered(self, hs):  # noqa: N802
        self.batch_calls += 1
        return [self.isRegistered(h) for h in hs]


@pytest.fixture
def reg():
    return FakeRegistry(
        {_hash("clean a"): "publisher", _hash("clean b"): "publisher", _hash("poison"): "attacker"},
        trusted={"publisher"},
    )


def test_chunk_text_from_str_and_dict():
    assert chunk_text("hello") == "hello"
    assert chunk_text({"text": "hello", "id": 1}) == "hello"


def test_chunk_text_rejects_bad_inputs():
    with pytest.raises(ValueError):
        chunk_text({"id": 1})
    with pytest.raises(TypeError):
        chunk_text({"text": 123})
    with pytest.raises(TypeError):
        chunk_text(42)  # type: ignore[arg-type]


def test_chunk_content_hash_matches_sha256_text():
    assert chunk_content_hash("abc") == _hash("abc")


def test_verified_mode_drops_attacker_registered_and_unregistered(reg):
    r = VerifiedRetriever(reg)
    chunks = ["clean a", "poison", "unknown", "clean b", "clean a"]
    assert r.filter(chunks) == ["clean a", "clean b", "clean a"]
    assert r.retrieve(chunks) == ["clean a", "clean b", "clean a"]


def test_registered_mode_shows_the_hole(reg):
    r = VerifiedRetriever(reg, mode="registered")
    assert r.filter(["clean a", "poison", "unknown"]) == ["clean a", "poison"]


def test_batch_view_used_once_per_filter_and_dedups(reg):
    r = VerifiedRetriever(reg)
    r.filter(["clean a", "clean a", "poison"])
    assert reg.batch_calls == 1


def test_single_call_fallback_without_batch_view():
    registry = MagicMock(spec=["isVerified"])
    ok = _hash("keep me")
    registry.isVerified.side_effect = lambda h: h == ok
    r = VerifiedRetriever(registry)
    assert r.filter([{"text": "keep me", "id": 1}, {"text": "nope"}]) == [{"text": "keep me", "id": 1}]
    assert registry.isVerified.call_count == 2


def test_use_batch_false_forces_single_calls(reg):
    r = VerifiedRetriever(reg, use_batch=False)
    assert r.filter(["clean b", "poison"]) == ["clean b"]
    assert reg.batch_calls == 0


def test_trust_revocation_changes_result(reg):
    r = VerifiedRetriever(reg)
    reg.trusted.clear()
    assert r.filter(["clean a", "poison"]) == []


def test_call_style_results_are_resolved():
    result = MagicMock()
    result.call.return_value = True
    registry = MagicMock(spec=["isVerified"])
    registry.isVerified.return_value = result
    assert VerifiedRetriever(registry).filter(["x"]) == ["x"]


def test_empty_and_bad_mode(reg):
    assert VerifiedRetriever(reg).filter([]) == []
    with pytest.raises(ValueError):
        VerifiedRetriever(reg, mode="anything")
    with pytest.raises(TypeError):
        VerifiedRetriever(object())


def test_batch_wrong_length_raises():
    class Bad:
        def areVerified(self, hs):  # noqa: N802
            return [True]

        def isVerified(self, h):  # noqa: N802
            return True

    with pytest.raises(RuntimeError):
        VerifiedRetriever(Bad()).filter(["a", "b"])
