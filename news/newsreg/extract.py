"""Deterministic, versioned extraction + normalization. The rules are NORMALIZATION.md (its sha256 is the spec version)."""
from __future__ import annotations

import hashlib
import unicodedata
from pathlib import Path

import trafilatura

HERE = Path(__file__).resolve().parent.parent
NORMALIZATION_SPEC = "0x" + hashlib.sha256((HERE / "NORMALIZATION.md").read_bytes()).hexdigest()
LOCK_SHA = hashlib.sha256((HERE / "requirements.lock").read_bytes()).hexdigest()
EXTRACTOR_VERSION = f"trafilatura-{trafilatura.__version__}/lock-{LOCK_SHA[:16]}"
MIN_PARAGRAPHS, MIN_CHARS = 3, 400


def norm_text(s: str) -> str:
    return " ".join(unicodedata.normalize("NFC", s).split())


def extract_paragraphs(raw: bytes) -> list[str]:
    txt = trafilatura.extract(raw, output_format="txt", favor_precision=True, include_comments=False, include_tables=False,
                              include_images=False, include_links=False, include_formatting=False, deduplicate=False,
                              with_metadata=False) or ""
    return [p for p in (norm_text(x) for x in txt.split("\n")) if p]


def is_substantial(paragraphs: list[str]) -> bool:
    return len(paragraphs) >= MIN_PARAGRAPHS and sum(len(p) for p in paragraphs) >= MIN_CHARS
