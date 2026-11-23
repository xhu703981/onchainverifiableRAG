"""Minimal SourceRegistry ABI + web3 connection helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from web3 import Web3

MINIMAL_ABI: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "isRegistered",
        "stateMutability": "view",
        "inputs": [{"name": "contentHash", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "type": "function",
        "name": "getSource",
        "stateMutability": "view",
        "inputs": [{"name": "contentHash", "type": "bytes32"}],
        "outputs": [
            {"name": "exists", "type": "bool"},
            {"name": "cid", "type": "string"},
            {"name": "sourceUri", "type": "string"},
            {"name": "registrant", "type": "address"},
            {"name": "timestamp", "type": "uint256"},
        ],
    },
    {
        "type": "function",
        "name": "isVerified",
        "stateMutability": "view",
        "inputs": [{"name": "contentHash", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "type": "function",
        "name": "areVerified",
        "stateMutability": "view",
        "inputs": [{"name": "contentHashes", "type": "bytes32[]"}],
        "outputs": [{"name": "out", "type": "bool[]"}],
    },
    {
        "type": "function",
        "name": "areRegistered",
        "stateMutability": "view",
        "inputs": [{"name": "contentHashes", "type": "bytes32[]"}],
        "outputs": [{"name": "out", "type": "bool[]"}],
    },
    {
        "type": "function",
        "name": "isTrustedPublisher",
        "stateMutability": "view",
        "inputs": [{"name": "publisher", "type": "address"}],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "type": "function",
        "name": "registerBatch",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "contentHashes", "type": "bytes32[]"}],
        "outputs": [{"name": "added", "type": "uint256"}],
    },
    {
        "type": "function",
        "name": "registerSource",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "contentHash", "type": "bytes32"},
            {"name": "cid", "type": "string"},
            {"name": "sourceUri", "type": "string"},
        ],
        "outputs": [],
    },
]


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_abi() -> list[dict[str, Any]]:
    """Load ABI from Foundry artifact if present, otherwise use the embedded ABI."""
    artifact = project_root() / "out" / "SourceRegistry.sol" / "SourceRegistry.json"
    if artifact.is_file():
        with artifact.open(encoding="utf-8") as handle:
            data = json.load(handle)
        abi = data.get("abi")
        if isinstance(abi, list) and abi:
            return abi
    return MINIMAL_ABI


def require_env(*names: str) -> dict[str, str]:
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise SystemExit(
            "Missing required environment variable(s): " + ", ".join(missing)
        )
    return {name: os.environ[name] for name in names}


def connect(rpc_url: str, registry_address: str) -> tuple[Web3, Any]:
    """Return a web3 instance and a SourceRegistry contract object."""
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    connected = getattr(w3, "is_connected", None)
    if callable(connected) and not connected():
        raise SystemExit(f"Could not connect to RPC at {rpc_url}")
    try:
        address = Web3.to_checksum_address(registry_address)
    except Exception as exc:  # noqa: BLE001 — surface a clean CLI error
        raise SystemExit(f"Invalid REGISTRY_ADDRESS: {registry_address}") from exc
    contract = w3.eth.contract(address=address, abi=load_abi())
    return w3, contract


ARC_TESTNET_CHAIN_ID = 5042002
ARC_MIN_BASE_FEE_WEI = 20 * 10**9  # Arc drops txs with maxFeePerGas < 20 gwei


def assert_chain(w3: Web3, expected: int | None) -> int:
    """Refuse to continue if the RPC is not on the expected chain (e.g. Arc testnet)."""
    chain_id = int(w3.eth.chain_id)
    if expected is not None and chain_id != int(expected):
        raise SystemExit(f"RPC chain id {chain_id} != expected {expected}; refusing to continue")
    return chain_id


def expected_chain_from_env() -> int | None:
    value = os.environ.get("EXPECTED_CHAIN_ID") or os.environ.get("ARC_CHAIN_ID")
    return int(value) if value else None


def fee_fields(w3: Web3) -> dict[str, int]:
    """EIP-1559 fee fields with Arc's 20 gwei floor respected."""
    base = int(w3.eth.get_block("latest").get("baseFeePerGas", ARC_MIN_BASE_FEE_WEI))
    tip = 10**9
    max_fee = max(2 * base + tip, ARC_MIN_BASE_FEE_WEI + tip)
    return {"maxFeePerGas": max_fee, "maxPriorityFeePerGas": tip}
