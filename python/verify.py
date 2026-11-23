#!/usr/bin/env python3
"""Compute a file's SHA-256 + CIDv1 and query SourceRegistry isRegistered / isVerified / getSource.

Required environment variables:
  RPC_URL             JSON-RPC endpoint
  REGISTRY_ADDRESS    deployed SourceRegistry address
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_DIR = Path(__file__).resolve().parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))

from hashing import SINGLE_BLOCK_LIMIT, cid_v1_raw, file_content_hash, sha256_file  # noqa: E402
from registry import assert_chain, connect, expected_chain_from_env, require_env  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="verify.py",
        description=(
            "Compute the SHA-256 content hash of a file (bytes32 / 0x hex) "
            "and query SourceRegistry.isRegistered and getSource."
        ),
        epilog="Required env vars: RPC_URL, REGISTRY_ADDRESS. Optional: EXPECTED_CHAIN_ID. Exit code 0 = verified, 2 = not verified.",
    )
    parser.add_argument(
        "file",
        type=Path,
        help="Path to the file whose content hash will be verified on-chain",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    path: Path = args.file
    if not path.is_file():
        print(f"error: not a file: {path}", file=sys.stderr)
        return 1

    env = require_env("RPC_URL", "REGISTRY_ADDRESS")
    content_hash = file_content_hash(path)
    print(f"file: {path.resolve()}")
    print(f"contentHash: {content_hash}")

    local_cid = cid_v1_raw(sha256_file(path)) if path.stat().st_size <= SINGLE_BLOCK_LIMIT else None
    if local_cid:
        print(f"localCid: {local_cid}")

    w3, contract = connect(env["RPC_URL"], env["REGISTRY_ADDRESS"])
    print(f"chainId: {assert_chain(w3, expected_chain_from_env())}")
    registered = bool(contract.functions.isRegistered(content_hash).call())
    print(f"isRegistered: {str(registered).lower()}")
    verified = bool(contract.functions.isVerified(content_hash).call())
    print(f"isVerified (registered by trusted publisher): {str(verified).lower()}")

    exists, cid, source_uri, registrant, timestamp = contract.functions.getSource(
        content_hash
    ).call()
    print(f"exists: {str(bool(exists)).lower()}")
    if exists:
        print(f"cid: {cid}")
        print(f"sourceUri: {source_uri}")
        print(f"registrant: {registrant}")
        print(f"timestamp: {timestamp}")
        trusted = bool(contract.functions.isTrustedPublisher(registrant).call())
        print(f"registrantTrusted: {str(trusted).lower()}")
        if local_cid and cid and cid != local_cid:
            print("WARNING: on-chain CID differs from locally computed CID")
    return 0 if verified else 2


if __name__ == "__main__":
    raise SystemExit(main())
