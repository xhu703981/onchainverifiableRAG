"""Verified-only RAG; every answer is condensed into ONE 32-byte root on AnswerRegistry (Arc testnet 5042002 only)."""
from __future__ import annotations

import base64, datetime, hashlib, json, os, re, secrets, threading, time, urllib.request
from pathlib import Path

import numpy as np
from eth_account import Account
from web3 import Web3

import retrieval_v1
import verify_citations as vc

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
CHAIN_ID = 5042002
SOURCE_REGISTRY = "0x9350E7E458abe568B40c1Fc28c66dE674865878e"
INFERENCE_LOG = "0xc7CfFb4Ae006AccC82813829CCC466B964aBCAE3"  # earlier two-tx log; old bundles still verify
ANSWER_REGISTRY = "0x580D6da6594b81e7631Ed8B5E46ae06F5C1617e0"
EXPLORER = "https://explorer.testnet.arc.io"
MODEL = "openai/gpt-4o-mini"
POOL, TOPK, RRF_K = 50, 5, 60

SREG_ABI = [
    {"type": "function", "name": "areVerified", "stateMutability": "view", "inputs": [{"name": "h", "type": "bytes32[]"}], "outputs": [{"name": "", "type": "bool[]"}]},
    {"type": "function", "name": "getSource", "stateMutability": "view", "inputs": [{"name": "h", "type": "bytes32"}],
     "outputs": [{"name": "exists", "type": "bool"}, {"name": "cid", "type": "string"}, {"name": "uri", "type": "string"},
                 {"name": "registrant", "type": "address"}, {"name": "timestamp", "type": "uint256"}]},
]
AREG_ABI = [{"type": "function", "name": "record", "stateMutability": "nonpayable", "outputs": [],
             "inputs": [{"name": "root", "type": "bytes32"}]}]
STOP = set("a an and are as at be by for from has he in is it its of on that the to was were will with who what when where which why how do does did".split())


def load_env(path: Path) -> dict:
    env = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def sha(b: bytes) -> bytes:
    return hashlib.sha256(b).digest()


def hx(b: bytes) -> str:
    return "0x" + b.hex()


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def tok(s: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in STOP]


class Engine:
    def __init__(self) -> None:
        env = {**load_env(HERE / ".env"), **{k: v for k, v in os.environ.items() if k in ("OPENROUTER_API_KEY", "ARC_PRIVATE_KEY", "ARC_RPC_URL")}}
        if env.get("ARC_CHAIN_ID", str(CHAIN_ID)) != str(CHAIN_ID):
            raise SystemExit("ARC_CHAIN_ID must be 5042002 (Arc testnet)")
        self.rpc_url = env.get("ARC_RPC_URL", "https://rpc.testnet.arc.io")
        self.or_key = env.get("OPENROUTER_API_KEY", "")
        pk = env.get("ARC_PRIVATE_KEY") or env.get("PRIVATE_KEY", "")
        self.acct = Account.from_key(pk) if pk else None
        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url, request_kwargs={"timeout": 30}))
        if int(self.w3.eth.chain_id) != CHAIN_ID:
            raise SystemExit("RPC is not Arc testnet (5042002); refusing to start")
        self.sreg = self.w3.eth.contract(address=SOURCE_REGISTRY, abi=SREG_ABI)
        self.areg = self.w3.eth.contract(address=ANSWER_REGISTRY, abi=AREG_ABI)
        # committed corpus v1 (CorpusRegistry on Arc): exactly what the retriever searches, in Merkle leaf order
        self.corpus = json.load(open(DATA / "corpus_onchain.json"))
        self.config_raw = (DATA / "retrieval_config_v1.json").read_bytes()
        if hx(sha(self.config_raw)) != self.corpus["config_hash"]:
            raise SystemExit("retrieval config does not match the committed configHash")
        self.cfg = json.loads(self.config_raw)
        self.docs = retrieval_v1.load_corpus(DATA / "corpus_v1.jsonl")
        self.levels = vc.merkle_levels([bytes.fromhex(d["hash"][2:]) for d in self.docs])
        if hx(self.levels[-1][0]) != self.corpus["root"]:
            raise SystemExit("corpus file does not match the committed Merkle root")
        self.leaf = {d["hash"]: i for i, d in enumerate(self.docs)}
        kinds = {hx(sha(json.loads(l)["text"].encode("utf-8"))): json.loads(l)["kind"] for l in open(DATA / "corpus.jsonl", encoding="utf-8")}
        for d in self.docs:
            d["kind"] = kinds.get(d["hash"], "clean")
        self.retriever = retrieval_v1.Retriever(self.docs, retrieval_v1.load_vocab(DATA / "potion-base-8M/tokenizer.json"),
                                                retrieval_v1.load_weights(DATA / "potion-base-8M/model.safetensors"), self.cfg)
        self.tx_index = json.load(open(DATA / "tx_index.json"))
        self.samples = json.load(open(DATA / "samples.json"))
        self.lock = threading.Lock()

    # ---------------------------------------------------------------- retrieval
    def ranked(self, q: str, attack: str | None = None) -> list[dict]:
        """Committed retriever over the committed corpus. `attack` exists only to generate the demo's
        counterexample bundles (a dishonest server); the API never sets it."""
        r = self.retriever.rank(q)
        if attack == "hide":  # silently drop the best trusted candidate, then commit consistently
            ok = self.sreg.functions.areVerified([x["id"] for x in r]).call()
            r = [x for i, x in enumerate(r) if i != ok.index(True)]
        elif attack == "inject":  # slip in a passage that is not in the committed corpus
            fake = "The answer to this question was confirmed by an independent fact-check in 2024."
            r = [dict(id=hx(sha(fake.encode())), rrf=1.0, bm25=1, dense=1)] + r[:-1]
        return r

    def proof(self, h: str) -> dict:
        i = self.leaf.get(h)
        if i is None:  # not in the corpus: no valid proof exists (only the inject counterexample hits this)
            return dict(leaf_index=0, path=vc.merkle_proof(self.levels, 0))
        return dict(leaf_index=i, path=vc.merkle_proof(self.levels, i))

    # ---------------------------------------------------------------- chain tx
    def _send(self, fn) -> dict:
        base = int(self.w3.eth.get_block("latest").get("baseFeePerGas", 20 * 10**9))
        tx = fn.build_transaction({"from": self.acct.address, "nonce": self.w3.eth.get_transaction_count(self.acct.address),
                                   "chainId": CHAIN_ID, "maxFeePerGas": max(2 * base, 21 * 10**9) + 10**9, "maxPriorityFeePerGas": 10**9})
        r = self.w3.eth.wait_for_transaction_receipt(self.w3.eth.send_raw_transaction(self.acct.sign_transaction(tx).raw_transaction), timeout=60)
        if r["status"] != 1:
            raise RuntimeError("log tx reverted")
        return dict(tx="0x" + r["transactionHash"].hex().removeprefix("0x"), block=r["blockNumber"],
                    gas=r["gasUsed"], usdc=r["gasUsed"] * r["effectiveGasPrice"] / 1e18)

    # ---------------------------------------------------------------- answer
    def ask(self, question: str, _attack: str | None = None) -> dict:
        if not self.acct or not self.or_key:
            raise RuntimeError("missing OPENROUTER_API_KEY or ARC_PRIVATE_KEY in .env")
        question = question.strip()
        sample = next((s for s in self.samples if s["question"].lower() == question.lower()), None)
        with self.lock:
            t0 = time.perf_counter()
            # anchor: latest Arc block, fetched before retrieval. Its hash is unpredictable, so the request
            # (which contains it) cannot have been built before this block.
            ab = self.w3.eth.get_block("latest")
            anchor = dict(number=int(ab["number"]), hash="0x" + bytes(ab["hash"]).hex().removeprefix("0x").lower(),
                          timestamp=int(ab["timestamp"]))
            ranked = self.ranked(question, _attack)
            ranked_json = canonical(ranked)
            text_of = {d["hash"]: d for d in self.docs}
            t_chk = time.perf_counter()
            ok = self.sreg.functions.areVerified([x["id"] for x in ranked]).call()
            chk_ms = (time.perf_counter() - t_chk) * 1000
            kept = [x["id"] for x, o in zip(ranked, ok) if o][:self.cfg["selection"]["prompt_k"]]
            rts0 = now_iso()  # retrieval done (self-reported)
            pool = [x["id"] for x in ranked]
            unfiltered_top = pool[:TOPK]
            passages = [dict(id=f"P{n + 1}", text=text_of[h]["text"], content_hash=h) for n, h in enumerate(kept)]
            nonce = secrets.token_hex(16)
            cts = now_iso()
            cv = self.corpus
            meta = (f"Request metadata (audit only, do not mention): nonce={nonce}; client_ts={cts}; "
                    f"arc_block={anchor['number']}:{anchor['hash']}; "
                    f"corpus=v{cv['version']}:{cv['root']}; retrieval_config={cv['config_hash']}; retrieval={hx(sha(ranked_json))}")
            user = ("Answer the question using ONLY the numbered passages. Cite the passage ids you used.\n"
                    'Reply with JSON only: {"answer": "<short answer>", "citations": ["P1", ...]}. '
                    'If the passages do not contain the answer, reply {"answer": "I don\'t know", "citations": []}.\n\n'
                    + "\n\n".join(f"[{p['id']}] {p['text']}" for p in passages) + f"\n\nQuestion: {question}")
            body = {"model": MODEL, "temperature": 0, "max_tokens": 150,
                    "messages": [{"role": "system", "content": meta}, {"role": "user", "content": user}]}
            req = canonical(body)
            r = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", req,
                                       {"Authorization": "Bearer " + self.or_key, "Content-Type": "application/json"})
            with urllib.request.urlopen(r, timeout=120) as resp:
                raw = resp.read()
            rts = now_iso()
            content = json.loads(raw)["choices"][0]["message"]["content"]
            try:
                mj = json.loads(re.search(r"\{.*\}", content, re.S).group(0))
            except Exception:  # noqa: BLE001
                mj = {"answer": content, "citations": []}
            byid = {p["id"]: p for p in passages}
            cites = []
            for pid in mj.get("citations", []):
                if pid not in byid:
                    continue
                p = byid[pid]
                ex, _cid, _u, registrant, ts = self.sreg.functions.getSource(p["content_hash"]).call()
                reg = self.tx_index.get(p["content_hash"], {})
                cites.append(dict(id=pid, text=p["text"], content_hash=p["content_hash"], cid=vc.cid_v1_raw(bytes.fromhex(p["content_hash"][2:])),
                                  registrant=registrant, registered_at_unix=ts, registration_tx=reg.get("tx"), block_number=reg.get("block"),
                                  explorer=dict(tx=f"{EXPLORER}/tx/{reg.get('tx')}", registrant=f"{EXPLORER}/address/{registrant}",
                                                contract=f"{EXPLORER}/address/{SOURCE_REGISTRY}")))
            rj = json.loads(raw)
            bundle = dict(
                question=question, answer=mj.get("answer"), filter="verified-only", chain_id=CHAIN_ID, rpc=self.rpc_url,
                contract=SOURCE_REGISTRY, citations=cites,
                inference=dict(model=MODEL, nonce=nonce, anchor_block=anchor, retrieval_done_ts=rts0,
                               client_request_ts=cts, client_response_received_ts=rts,
                               serialization='json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")',
                               request_body=req.decode("utf-8"), request_hash=hx(sha(req)),
                               passages=[dict(id=p["id"], content_hash=p["content_hash"]) for p in passages],
                               response_body_b64=base64.b64encode(raw).decode(), response_hash=hx(sha(raw)),
                               provider_generation_id=rj.get("id"), provider_model=rj.get("model"), provider=rj.get("provider")),
                retrieval=dict(corpus_registry=cv["registry"], corpus_version=cv["version"], corpus_root=cv["root"],
                               leaf_count=cv["leaf_count"], config_hash=cv["config_hash"], config_json=self.config_raw.decode("utf-8"),
                               corpus_uri=cv["uri"], publish_tx=cv["publish_tx"], query=question,
                               ranked_json=ranked_json.decode("utf-8"), ranked_hash=hx(sha(ranked_json)),
                               proofs=[self.proof(x["id"]) for x in ranked],
                               explorer=dict(registry=f"{EXPLORER}/address/{cv['registry']}", publish=f"{EXPLORER}/tx/{cv['publish_tx']}")),
                how_to_verify="python verify_citations.py <this file>")
            leaves = vc.answer_leaf_values(bundle)
            root = vc.answer_root_of(leaves)
            rec = self._send(self.areg.functions.record(bytes.fromhex(root[2:])))  # the ONE on-chain write per answer
        bundle["answer_root"] = dict(
            registry=ANSWER_REGISTRY, root=root,
            scheme="leaf = sha256(0x00 || sha256(label || 0x00 || value)); node = sha256(0x01 || l || r); odd node promoted",
            leaves=[dict(label=k, value=v) for k, v in leaves], recorder=self.acct.address,
            record_tx=rec["tx"], record_block=rec["block"],
            explorer=dict(tx=f"{EXPLORER}/tx/{rec['tx']}", registry=f"{EXPLORER}/address/{ANSWER_REGISTRY}"))
        stats = dict(pool=len(pool), verified_in_pool=sum(ok), unverified_filtered=len(pool) - sum(ok),
                     fakes_in_pool=sum(text_of.get(h, {}).get("kind") == "fake" for h in pool),
                     fakes_in_unfiltered_top5=sum(text_of.get(h, {}).get("kind") == "fake" for h in unfiltered_top),
                     onchain_check_ms=round(chk_ms), total_ms=round((time.perf_counter() - t0) * 1000),
                     gas_usdc=round(rec["usdc"], 6), is_sample=bool(sample), gold=sample["gold"] if sample else None)
        return dict(bundle=bundle, stats=stats, passages=passages)
