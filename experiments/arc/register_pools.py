"""Register experiment passages on the LIVE Arc testnet SourceRegistry (chain 5042002 only).

Phases (run in order):
  fund      deployer -> attacker native USDC transfer (tiny)
  attacker  attacker wallet registerBatch()es ALL 500 PoisonedRAG NQ fake passages (it is NOT trusted)
  cov50 / cov80 / cov100   trusted deployer registerBatch()es the clean passages of the 30 questions'
            fused hybrid candidate pools (top-50), cumulative 50% / 80% / 100% in a fixed hash order.
Content hashed = SHA-256 of the exact UTF-8 passage text given to the LLM.
Every tx is appended to arc/registrations.json."""
import hashlib, json, os, sys, time
from pathlib import Path
from eth_account import Account
from web3 import Web3
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT / "python"))
from registry import connect, assert_chain, fee_fields
REG = "0x9350E7E458abe568B40c1Fc28c66dE674865878e"; CHAIN = 5042002; BATCH = 250
def envfile(p):
    return dict(l.split("=", 1) for l in Path(p).read_text().split() if "=" in l)
dep = envfile(ROOT / ".env.arc"); att = envfile(ROOT / "experiments/arc/.env.attacker")
w3, c = connect(dep["ARC_RPC_URL"], REG); assert_chain(w3, CHAIN)
LOG = ROOT / "experiments/arc/registrations.json"
log = json.load(open(LOG)) if LOG.exists() else []

def h(t): return "0x" + hashlib.sha256(t.encode("utf-8")).hexdigest()
items = json.load(open(ROOT / "experiments/hybrid_baseline/retrieval_n30.json"))["items"]
clean_txt = {}
for it in items:
    for cnd in ("clean", "poisoned"):
        for d in it["pool"][cnd]:
            if not d.startswith("adv:"): clean_txt[d] = it["texts"][d]
clean_hashes = sorted({h(t) for t in clean_txt.values()}, key=lambda x: hashlib.sha256(b"cov" + bytes.fromhex(x[2:])).digest())
n = len(clean_hashes); cut = {"cov50": n * 50 // 100, "cov80": n * 80 // 100, "cov100": n}
adv = json.load(open(ROOT / "experiments/data/nq_adv.json"))
fake_hashes = sorted({h(a["question"] + "." + s) for a in adv.values() for s in a["adv_texts"]})
json.dump(dict(clean_order=clean_hashes, cuts=cut, fakes=fake_hashes),
          open(ROOT / "experiments/arc/hash_manifest.json", "w"), indent=0)

def send(acct, fn=None, tx_extra=None, note=""):
    tx = dict(tx_extra or {})
    tx.update({"from": acct.address, "nonce": w3.eth.get_transaction_count(acct.address), "chainId": CHAIN, **fee_fields(w3)})
    tx = fn.build_transaction(tx) if fn is not None else {**tx, "gas": 21000}
    t0 = time.time()
    rcpt = w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(acct.sign_transaction(tx).raw_transaction))
    rec = dict(note=note, tx="0x" + rcpt["transactionHash"].hex().removeprefix("0x"), status=rcpt["status"], gasUsed=rcpt["gasUsed"],
               effectiveGasPrice=rcpt["effectiveGasPrice"], cost_usdc=rcpt["gasUsed"] * rcpt["effectiveGasPrice"] / 1e18,
               block=rcpt["blockNumber"], from_=acct.address, seconds_to_receipt=round(time.time() - t0, 2))
    log.append(rec); json.dump(log, open(LOG, "w"), indent=1); print(rec)
    assert rcpt["status"] == 1
def batches(acct, hashes, note):
    todo = [x for x, ok in zip(hashes, c.functions.areRegistered(hashes).call()) if not ok]
    print(note, "to register:", len(todo), "of", len(hashes))
    for i in range(0, len(todo), BATCH):
        send(acct, c.functions.registerBatch(todo[i:i + BATCH]), note=f"{note} [{i}:{i + len(todo[i:i+BATCH])}]")

phase = sys.argv[1]
deployer = Account.from_key(dep["PRIVATE_KEY"]); attacker = Account.from_key(att["ATTACKER_PRIVATE_KEY"])
assert deployer.address == dep["DEPLOYER_ADDRESS"] and attacker.address == att["ATTACKER_ADDRESS"]
if phase == "fund":
    send(deployer, tx_extra={"to": attacker.address, "value": Web3.to_wei(0.5, "ether")}, note="fund attacker 0.5 USDC")
elif phase == "attacker":
    assert not c.functions.isTrustedPublisher(attacker.address).call()
    batches(attacker, fake_hashes, "attacker registers fakes")
elif phase in cut:
    batches(deployer, clean_hashes[:cut[phase]], f"trusted {phase}")
print("deployer", w3.eth.get_balance(deployer.address) / 1e18, "attacker", w3.eth.get_balance(attacker.address) / 1e18,
      "batchSourceCount", c.functions.batchSourceCount().call())
