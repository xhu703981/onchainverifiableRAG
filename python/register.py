#!/usr/bin/env python3
"""Batch-register files in a folder with SourceRegistry.registerSource.

Walks a directory recursively, SHA-256 hashes each file, skips hashes that
are already registered, and submits registerSource(hash, cid, sourceUri), or with
--batch N, registerBatch(hashes) in chunks of N (one storage slot per hash; the CID
is derived on-chain from the hash via cidOf).

CID is a real CIDv1 (raw codec, sha2-256), identical to
`ipfs add --only-hash --cid-version 1` for files <= 256 KiB; larger files fall back
to a placeholder (and cannot use --batch).
sourceUri is the file's absolute file:// URI (or path).
Optional: EXPECTED_CHAIN_ID (e.g. 5042002 for Arc testnet) -- refuse other chains.

Required environment variables:
  RPC_URL             JSON-RPC endpoint
  REGISTRY_ADDRESS    deployed SourceRegistry address
  PRIVATE_KEY         hex key used to sign registerSource txs (not needed with --dry-run)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_DIR = Path(__file__).resolve().parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))

from eth_account import Account  # noqa: E402
from web3 import Web3  # noqa: E402

from hashing import SINGLE_BLOCK_LIMIT, cid_v1_raw, file_content_hash, sha256_file  # noqa: E402
from registry import assert_chain, connect, expected_chain_from_env, fee_fields, require_env  # noqa: E402

SKIP_DIR_NAMES = {".git", ".venv", "__pycache__", "out", "cache", "lib", "node_modules"}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="register.py",
        description=(
            "Recursively hash files in a folder and register new content hashes "
            "on SourceRegistry. Already-registered hashes are skipped."
        ),
        epilog=(
            "Required env vars: RPC_URL, REGISTRY_ADDRESS. "
            "PRIVATE_KEY is required unless --dry-run is set."
        ),
    )
    parser.add_argument(
        "folder",
        type=Path,
        help="Root folder to walk recursively",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the actions that would be taken without sending transactions",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=0,
        metavar="N",
        help="Use registerBatch with up to N hashes per transaction (0 = registerSource per file)",
    )
    return parser.parse_args(argv)


def file_cid(path: Path) -> str | None:
    if path.stat().st_size > SINGLE_BLOCK_LIMIT:
        return None
    return cid_v1_raw(sha256_file(path))


def iter_files(folder: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        files.append(path)
    return files


def placeholder_cid(content_hash: str) -> str:
    prefix = content_hash[2:10] if content_hash.startswith("0x") else content_hash[:8]
    return f"placeholder-cid-{prefix}"


def source_uri_for(path: Path) -> str:
    return path.resolve().as_uri()


def raw_signed_tx(signed) -> bytes:
    raw = getattr(signed, "raw_transaction", None)
    if raw is None:
        raw = getattr(signed, "rawTransaction")
    return raw


def send_register(w3: Web3, contract, account, content_hash: str, cid: str, source_uri: str):
    return _send(w3, account, contract.functions.registerSource(content_hash, cid, source_uri))


def send_batch(w3: Web3, contract, account, hashes: list[str]):
    return _send(w3, account, contract.functions.registerBatch(hashes))


def _send(w3: Web3, account, fn):
    tx = fn.build_transaction(
        {
            "from": account.address,
            "nonce": w3.eth.get_transaction_count(account.address),
            "chainId": w3.eth.chain_id,
            **fee_fields(w3),
        }
    )
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(raw_signed_tx(signed))
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    return receipt


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    folder: Path = args.folder
    if not folder.is_dir():
        print(f"error: not a directory: {folder}", file=sys.stderr)
        return 1

    required = ["RPC_URL", "REGISTRY_ADDRESS"]
    if not args.dry_run:
        required.append("PRIVATE_KEY")
    env = require_env(*required)

    w3, contract = connect(env["RPC_URL"], env["REGISTRY_ADDRESS"])
    assert_chain(w3, expected_chain_from_env())
    account = None
    if not args.dry_run:
        try:
            account = Account.from_key(env["PRIVATE_KEY"])
        except Exception as exc:  # noqa: BLE001
            print(f"error: invalid PRIVATE_KEY: {exc}", file=sys.stderr)
            return 1

    files = iter_files(folder)
    if not files:
        print(f"No files found under {folder.resolve()}")
        return 0

    registered_count = 0
    skipped_count = 0
    failed_count = 0

    if args.batch > 0:
        return _main_batch(args, w3, contract, account, files)

    for path in files:
        content_hash = file_content_hash(path)
        cid = file_cid(path) or placeholder_cid(content_hash)
        uri = source_uri_for(path)
        already = bool(contract.functions.isRegistered(content_hash).call())
        if already:
            print(f"[skip] already registered  {content_hash}  {path}")
            skipped_count += 1
            continue

        if args.dry_run:
            print(f"[dry-run] would register  {content_hash}  cid={cid}  uri={uri}  {path}")
            registered_count += 1
            continue

        print(f"[register] {content_hash}  cid={cid}  uri={uri}  {path}")
        try:
            receipt = send_register(w3, contract, account, content_hash, cid, uri)
            status = int(receipt.get("status", 0))
            tx_hash = receipt.get("transactionHash")
            if hasattr(tx_hash, "hex"):
                tx_hex = tx_hash.hex()
                if not tx_hex.startswith("0x"):
                    tx_hex = "0x" + tx_hex
            else:
                tx_hex = str(tx_hash)
            if status != 1:
                print(f"  failed tx={tx_hex} status={status}", file=sys.stderr)
                failed_count += 1
                continue
            print(f"  tx={tx_hex}  block={receipt.get('blockNumber')}")
            registered_count += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  error: {exc}", file=sys.stderr)
            failed_count += 1

    if args.dry_run:
        print(
            f"Dry-run: would register {registered_count} file(s), "
            f"skip {skipped_count} already registered. No transactions sent."
        )
    else:
        print(
            f"Done: registered {registered_count}, skipped {skipped_count}, "
            f"failed {failed_count}."
        )
    return 1 if failed_count else 0


def _main_batch(args, w3, contract, account, files) -> int:
    pending: list[str] = []
    for path in files:
        if path.stat().st_size > SINGLE_BLOCK_LIMIT:
            print(f"[skip] >256 KiB, CID not derivable from sha256; use non-batch mode  {path}")
            continue
        h = file_content_hash(path)
        if bool(contract.functions.isRegistered(h).call()):
            print(f"[skip] already registered  {h}  {path}")
            continue
        print(f"[{'dry-run' if args.dry_run else 'queue'}] {h}  cid={file_cid(path)}  {path}")
        pending.append(h)
    pending = list(dict.fromkeys(pending))
    if args.dry_run:
        print(f"Dry-run: would batch-register {len(pending)} hash(es) in "
              f"{-(-len(pending) // args.batch)} tx(s). No transactions sent.")
        return 0
    failed = 0
    for i in range(0, len(pending), args.batch):
        chunk = pending[i:i + args.batch]
        receipt = send_batch(w3, contract, account, chunk)
        tx = receipt.get("transactionHash")
        tx_hex = tx.hex() if hasattr(tx, "hex") else str(tx)
        tx_hex = tx_hex if tx_hex.startswith("0x") else "0x" + tx_hex
        ok = int(receipt.get("status", 0)) == 1
        failed += 0 if ok else 1
        print(f"  batch {i // args.batch}: {len(chunk)} hashes  tx={tx_hex}  "
              f"gasUsed={receipt.get('gasUsed')}  status={'ok' if ok else 'FAILED'}")
    print(f"Done: submitted {len(pending)} hash(es); failed batches {failed}.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
