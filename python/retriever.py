"""Filter RAG chunks by on-chain provenance.

Default mode is ``"verified"``: a chunk is kept only if its SHA-256 content hash is
registered AND its registrant is a trusted publisher (``SourceRegistry.isVerified``).
``"registered"`` mode (``isRegistered`` only) is kept for comparison: it is NOT a
defense, because registration is permissionless and an attacker can register poison.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence, Union

from hashing import sha256_text, to_bytes32_hex

Chunk = Union[str, Mapping[str, Any]]
MODES = ("verified", "registered")


def chunk_text(chunk: Chunk) -> str:
    """Extract the text used for hashing from a chunk (str or dict with 'text')."""
    if isinstance(chunk, str):
        return chunk
    if isinstance(chunk, Mapping):
        if "text" not in chunk:
            raise ValueError("dict chunks must include a 'text' field")
        text = chunk["text"]
        if not isinstance(text, str):
            raise TypeError("chunk['text'] must be a string")
        return text
    raise TypeError(f"unsupported chunk type: {type(chunk).__name__}")


def chunk_content_hash(chunk: Chunk) -> str:
    """SHA-256 of the chunk text (UTF-8) as 0x-prefixed bytes32 hex."""
    return to_bytes32_hex(sha256_text(chunk_text(chunk)))


def _resolve(registry: Any, name: str) -> Callable[..., Any] | None:
    """Find `name` either on registry.functions (web3 Contract) or on registry itself."""
    functions = getattr(registry, "functions", None)
    if functions is not None and type(registry).__module__.startswith("web3"):
        fn = getattr(functions, name)
        return lambda *a: fn(*a).call()
    direct = getattr(registry, name, None)
    if callable(direct):
        def call(*a):
            result = direct(*a)
            c = getattr(result, "call", None)
            return c() if callable(c) else result
        return call
    if functions is not None and hasattr(functions, name):
        fn = getattr(functions, name)
        return lambda *a: fn(*a).call()
    return None


class VerifiedRetriever:
    """Keeps only chunks whose provenance passes the on-chain check.

    `registry` is a web3 SourceRegistry contract or any object exposing
    `isVerified(hash)` / `isRegistered(hash)` (and optionally the batch views
    `areVerified(list)` / `areRegistered(list)`, used when available: one RPC call).
    """

    def __init__(self, registry: Any, mode: str = "verified", use_batch: bool = True) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self.registry = registry
        self.mode = mode
        single = "isVerified" if mode == "verified" else "isRegistered"
        batch = "areVerified" if mode == "verified" else "areRegistered"
        self._single = _resolve(registry, single)
        self._batch = _resolve(registry, batch) if use_batch else None
        if self._single is None and self._batch is None:
            raise TypeError(f"registry exposes neither {single} nor {batch}")

    def check(self, hashes: Sequence[str]) -> list[bool]:
        if not hashes:
            return []
        if self._batch is not None:
            out = [bool(x) for x in self._batch(list(hashes))]
            if len(out) != len(hashes):
                raise RuntimeError("batch view returned wrong length")
            return out
        return [bool(self._single(h)) for h in hashes]

    def filter(self, chunks: Sequence[Chunk]) -> list[Chunk]:
        """Return the subset of `chunks` (order preserved) that pass the check."""
        hashes = [chunk_content_hash(c) for c in chunks]
        unique = list(dict.fromkeys(hashes))
        ok = dict(zip(unique, self.check(unique)))
        return [c for c, h in zip(chunks, hashes) if ok[h]]

    def retrieve(self, chunks: Sequence[Chunk]) -> list[Chunk]:
        """Alias for :meth:`filter`."""
        return self.filter(chunks)
