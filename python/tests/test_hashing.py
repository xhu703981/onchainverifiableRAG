"""Known-answer tests for SHA-256 helpers (no network)."""

from __future__ import annotations

from pathlib import Path

from hashing import file_content_hash, sha256_bytes, sha256_file, sha256_text, to_bytes32_hex

# FIPS 180-2 / NIST known-answer vectors
SHA256_EMPTY = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
SHA256_ABC = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_sha256_text_empty_string():
    digest = sha256_text("")
    assert digest.hex() == SHA256_EMPTY
    assert to_bytes32_hex(digest) == "0x" + SHA256_EMPTY


def test_sha256_text_abc():
    digest = sha256_text("abc")
    assert digest.hex() == SHA256_ABC
    assert to_bytes32_hex(digest) == "0x" + SHA256_ABC


def test_sha256_bytes_matches_text_encoding():
    assert sha256_bytes(b"abc") == sha256_text("abc")
    assert sha256_bytes(b"") == sha256_text("")


def test_sha256_file_matches_bytes(tmp_path: Path):
    path = tmp_path / "abc.txt"
    path.write_bytes(b"abc")
    assert sha256_file(path) == sha256_bytes(b"abc")
    assert file_content_hash(path) == "0x" + SHA256_ABC


def test_sha256_file_empty(tmp_path: Path):
    path = tmp_path / "empty.bin"
    path.write_bytes(b"")
    assert sha256_file(path) == sha256_bytes(b"")
    assert file_content_hash(path) == "0x" + SHA256_EMPTY


def test_sha256_file_streamed_chunks(tmp_path: Path):
    path = tmp_path / "chunked.bin"
    data = b"0123456789" * 10_000  # 100 KiB, larger than the test chunk size
    path.write_bytes(data)
    assert sha256_file(path, chunk_size=1024) == sha256_bytes(data)


def test_to_bytes32_hex_rejects_wrong_length():
    try:
        to_bytes32_hex(b"short")
    except ValueError as exc:
        assert "32 bytes" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_cid_v1_raw_matches_kubo_vector():
    # kubo 0.32.1: printf 'hello world' | ipfs add --only-hash --cid-version 1 -Q
    from hashing import cid_v1_raw, sha256_bytes, text_cid

    expected = "bafkreifzjut3te2nhyekklss27nh3k72ysco7y32koao5eei66wof36n5e"
    assert cid_v1_raw(sha256_bytes(b"hello world")) == expected
    assert text_cid("hello world") == expected
    assert len(cid_v1_raw(b"\x00" * 32)) == 59


def test_cid_v1_raw_rejects_bad_input():
    import pytest
    from hashing import cid_v1_raw, text_cid

    with pytest.raises(ValueError):
        cid_v1_raw(b"short")
    with pytest.raises(ValueError):
        text_cid("x" * (256 * 1024 + 1))
