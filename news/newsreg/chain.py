"""Minimal Arc TESTNET client: JSON-RPC + locally signed EIP-1559 txs. Refuses any other chain.
The private key is read from the environment / .env.arc and never printed."""
from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

from eth_abi import decode, encode
from eth_account import Account
from eth_utils import keccak, to_checksum_address

CHAIN_ID = 5042002
RPC_URL = "https://rpc.testnet.arc.io"
EXPLORER = "https://explorer.testnet.arc.io"
REPO = Path(__file__).resolve().parents[2]
DEPLOY_FILE = REPO / "news" / "deployment.json"


def load_env() -> dict:
    env = {}
    for f in (REPO / ".env.arc", REPO / "webdemo" / ".env"):
        if f.exists():
            for line in f.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    env.update({k: v for k, v in os.environ.items() if k in ("ARC_PRIVATE_KEY", "PRIVATE_KEY", "ARC_RPC_URL", "OPENROUTER_API_KEY")})
    return env


class RPC:
    def __init__(self, url: str = RPC_URL) -> None:
        self.url, self.n = url, 0

    def __call__(self, method: str, params: list):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        for attempt in range(4):
            try:
                req = urllib.request.Request(self.url, body, {"Content-Type": "application/json", "User-Agent": "newsreg/0.1"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    out = json.loads(r.read())
                self.n += 1
                if "error" in out:
                    raise RuntimeError(f"RPC error {out['error']}")
                return out["result"]
            except (OSError, TimeoutError):
                if attempt == 3:
                    raise
                time.sleep(1.5 * (attempt + 1))

    def check_chain(self) -> None:
        cid = int(self("eth_chainId", []), 16)
        if cid != CHAIN_ID:
            raise SystemExit(f"RPC chain id {cid} is not Arc testnet {CHAIN_ID}; refusing")


def selector(sig: str) -> bytes:
    return keccak(text=sig)[:4]


def call(rpc: RPC, to: str, sig: str, types: list[str], args: list, out_types: list[str]):
    data = selector(sig) + encode(types, args)
    res = rpc("eth_call", [{"to": to, "data": "0x" + data.hex()}, "latest"])
    return decode(out_types, bytes.fromhex(res[2:]))


class Signer:
    def __init__(self, key: str, rpc: RPC) -> None:
        self.acct = Account.from_key(key)
        self.address = self.acct.address
        self.rpc = rpc
        rpc.check_chain()

    @classmethod
    def from_env(cls, rpc: RPC) -> "Signer":
        env = load_env()
        k = env.get("ARC_PRIVATE_KEY") or env.get("PRIVATE_KEY")
        if not k:
            raise SystemExit("no ARC_PRIVATE_KEY / PRIVATE_KEY in env or .env.arc")
        return cls(k, rpc)

    def send(self, to: str | None, data: bytes, value: int = 0, gas: int | None = None) -> dict:
        self.rpc.check_chain()
        blk = self.rpc("eth_getBlockByNumber", ["latest", False])
        base = int(blk.get("baseFeePerGas", hex(20 * 10**9)), 16)
        tx = {"chainId": CHAIN_ID, "nonce": int(self.rpc("eth_getTransactionCount", [self.address, "pending"]), 16),
              "maxFeePerGas": max(2 * base, 21 * 10**9) + 10**9, "maxPriorityFeePerGas": 10**9, "value": value,
              "data": "0x" + data.hex(), "type": 2}
        if to:
            tx["to"] = to_checksum_address(to)
        est = {"from": self.address, "data": tx["data"], "value": hex(value)}
        if to:
            est["to"] = to
        tx["gas"] = gas or int(int(self.rpc("eth_estimateGas", [est]), 16) * 1.3) + 10_000
        raw = self.acct.sign_transaction(tx).raw_transaction
        h = self.rpc("eth_sendRawTransaction", ["0x" + raw.hex().removeprefix("0x")])
        for _ in range(120):
            r = self.rpc("eth_getTransactionReceipt", [h])
            if r:
                break
            time.sleep(1)
        else:
            raise RuntimeError(f"tx {h} not mined in 120 s")
        if int(r["status"], 16) != 1:
            raise RuntimeError(f"tx {h} reverted")
        b = self.rpc("eth_getBlockByNumber", [r["blockNumber"], False])
        return dict(tx=h, block=int(r["blockNumber"], 16), block_time=int(b["timestamp"], 16), receipt=r,
                    gas=int(r["gasUsed"], 16), cost_usdc=int(r["gasUsed"], 16) * int(r["effectiveGasPrice"], 16) / 1e18)


def deployment() -> dict:
    return json.loads(DEPLOY_FILE.read_text())


REGISTER_SIG = "registerBatch(bytes32,bytes32,uint32,string)"


def register_batch(signer: Signer, registry: str, root: bytes, manifest_hash: bytes, count: int, uri: str) -> dict:
    data = selector(REGISTER_SIG) + encode(["bytes32", "bytes32", "uint32", "string"], [root, manifest_hash, count, uri])
    return signer.send(registry, data)


def is_trusted_roots(rpc: RPC, registry: str, roots: list[bytes]) -> list[bool]:
    if not roots:
        return []
    return list(call(rpc, registry, "areTrustedRoots(bytes32[])", ["bytes32[]"], [roots], ["bool[]"])[0])
