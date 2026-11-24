"""Fetch step behind an interface, so a zkTLS fetcher (trust level L2) can replace HttpFetcher later
without touching extraction, hashing or registration. L0 today: our crawler's word."""
from __future__ import annotations

import datetime as dt
import gzip
import zlib
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol

UA = "Mozilla/5.0 (compatible; VerifiableNewsBot/0.1; +https://github.com/xhu703981/onchainverifiableRAG)"
MAX_BYTES = 8 * 1024 * 1024


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class FetchResult:
    url: str          # requested URL
    final_url: str    # after redirects
    status: int
    body: bytes       # exact response body bytes
    fetched_at: str   # ISO 8601 UTC "Z", claimed by the fetcher
    attestation: dict = field(default_factory=dict)  # L0: empty. L2 (zkTLS): the TLS transcript proof goes here.


class Fetcher(Protocol):
    trust_level: str

    def fetch(self, url: str) -> FetchResult: ...


class HttpFetcher:
    trust_level = "L0"

    def __init__(self, timeout: float = 25.0, user_agent: str = UA) -> None:
        self.timeout, self.ua = timeout, user_agent

    def fetch(self, url: str) -> FetchResult:
        req = urllib.request.Request(url, headers={"User-Agent": self.ua, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                                                   "Accept-Language": "en"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            body = r.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise ValueError("response too large")
            enc = (r.headers.get("Content-Encoding") or "").lower()
            if enc == "gzip" or body[:2] == b"\x1f\x8b":  # some servers gzip unasked; store the decoded entity body
                body = gzip.decompress(body)
            elif enc == "deflate":
                body = zlib.decompress(body)
            return FetchResult(url=url, final_url=r.geturl(), status=r.status, body=body, fetched_at=utc_now())
