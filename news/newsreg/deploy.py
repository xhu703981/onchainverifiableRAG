"""Deploy NewsRegistry to Arc TESTNET (5042002) and trust the crawler key. Usage: python -m newsreg.deploy"""
import json
import subprocess

from eth_abi import encode

from .chain import CHAIN_ID, DEPLOY_FILE, EXPLORER, REPO, RPC, Signer, selector


def main() -> None:
    rpc = RPC()
    s = Signer.from_env(rpc)
    if DEPLOY_FILE.exists():
        raise SystemExit(f"already deployed: {DEPLOY_FILE}")
    subprocess.run(["forge", "build"], cwd=REPO, check=True, capture_output=True)
    art = json.loads((REPO / "out" / "NewsRegistry.sol" / "NewsRegistry.json").read_text())
    code = bytes.fromhex(art["bytecode"]["object"].removeprefix("0x"))
    d = s.send(None, code)
    addr = d["receipt"]["contractAddress"]
    t = s.send(addr, selector("addTrustedPublisher(address)") + encode(["address"], [s.address]))
    out = dict(chain_id=CHAIN_ID, news_registry=addr, owner=s.address, crawler=s.address, deploy_tx=d["tx"], deploy_block=d["block"],
               trust_tx=t["tx"], explorer=f"{EXPLORER}/address/{addr}")
    DEPLOY_FILE.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
