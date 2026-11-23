"""Shared SHA-256 helpers for on-chain content hashes (bytes32 / 0x-prefixed hex)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Union

DEFAULT_CHUNK_SIZE = 1024 * 1024  # 1 MiB


def sha256_bytes(data: bytes) -> bytes:
    """Return the raw 32-byte SHA-256 digest of `data`."""
    return hashlib.sha256(data).digest()


def sha256_text(text: str) -> bytes:
    """Return the SHA-256 digest of `text` encoded as UTF-8."""
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Union[str, Path], chunk_size: int = DEFAULT_CHUNK_SIZE) -> bytes:
    """Return the SHA-256 digest of a file, read in streamed chunks."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.digest()


def to_bytes32_hex(digest: bytes) -> str:
    """Format a 32-byte digest as a 0x-prefixed hex string (bytes32)."""
    if not isinstance(digest, (bytes, bytearray)):
        raise TypeError("digest must be bytes")
    if len(digest) != 32:
        raise ValueError(f"digest must be 32 bytes, got {len(digest)}")
    return "0x" + bytes(digest).hex()


def file_content_hash(path: Union[str, Path]) -> str:
    """Convenience: SHA-256 a file and return it as 0x-prefixed bytes32 hex."""
    return to_bytes32_hex(sha256_file(path))


_B32 = "abcdefghijklmnopqrstuvwxyz234567"


def cid_v1_raw(digest: bytes) -> str:
    """CIDv1 (raw codec 0x55, sha2-256 multihash), base32-lower multibase ("b...").

    Matches `ipfs add --cid-version 1 --only-hash` (kubo uses raw leaves for CIDv1)
    for files that fit in a single block (<= 256 KiB with the default chunker), and
    matches SourceRegistry.cidOf(digest) on-chain.
    """
    if len(digest) != 32:
        raise ValueError("digest must be 32 bytes")
    data = bytes.fromhex("01551220") + bytes(digest)
    out, buf, bits = ["b"], 0, 0
    for byte in data:
        buf = (buf << 8) | byte
        bits += 8
        while bits >= 5:
            bits -= 5
            out.append(_B32[(buf >> bits) & 31])
    if bits:
        out.append(_B32[(buf << (5 - bits)) & 31])
    return "".join(out)


SINGLE_BLOCK_LIMIT = 256 * 1024


def text_cid(text: str) -> str:
    data = text.encode("utf-8")
    if len(data) > SINGLE_BLOCK_LIMIT:
        raise ValueError("content larger than one IPFS block; CID is not sha256(content)")
    return cid_v1_raw(sha256_bytes(data))
