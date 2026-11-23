"""Verified-only hybrid RAG with an on-chain inference log (Arc testnet 5042002 only)."""
from __future__ import annotations

import base64, datetime, hashlib, json, os, re, secrets, threading, time, urllib.request
from pathlib import Path

import numpy as np
from eth_account import Account
from model2vec import StaticModel
from rank_bm25 import BM25Okapi
from web3 import Web3

import verify_citations as vc

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
CHAIN_ID = 5042002
SOURCE_REGISTRY = "0x9350E7E458abe568B40c1Fc28c66dE674865878e"
INFERENCE_LOG = "0xc7CfFb4Ae006AccC82813829CCC466B964aBCAE3"
EXPLORER = "https://explorer.testnet.arc.io"
MODEL = "openai/gpt-4o-mini"
POOL, TOPK, RRF_K = 50, 5, 60

SREG_ABI = [
    {"type": "function", "name": "areVerified", "stateMutability": "view", "inputs": [{"name": "h", "type": "bytes32[]"}], "outputs": [{"name": "", "type": "bool[]"}]},
    {"type": "function", "name": "getSource", "stateMutability": "view", "inputs": [{"name": "h", "type": "bytes32"}],
     "outputs": [{"name": "exists", "type": "bool"}, {"name": "cid", "type": "string"}, {"name": "uri", "type": "string"},
                 {"name": "registrant", "type": "address"}, {"name": "timestamp", "type": "uint256"}]},
]
ILOG_ABI = [
    {"type": "function", "name": "commit", "stateMutability": "nonpayable", "outputs": [],
     "inputs": [{"name": n, "type": t} for n, t in [("answerId", "bytes32"), ("questionId", "bytes32"), ("requestHash", "bytes32"),
                                                     ("passagesRoot", "bytes32"), ("modelHash", "bytes32"), ("passageCount", "uint16")]]},
    {"type": "function", "name": "finalize", "stateMutability": "nonpayable", "outputs": [],
     "inputs": [{"name": "answerId", "type": "bytes32"}, {"name": "responseHash", "type": "bytes32"}]},
]
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
        self.ilog = self.w3.eth.contract(address=INFERENCE_LOG, abi=ILOG_ABI)
        self.docs = [json.loads(l) for l in open(DATA / "corpus.jsonl", encoding="utf-8")]
        for d in self.docs:
            d["hash"] = hx(sha(d["text"].encode("utf-8")))
        self.bm25 = BM25Okapi([tok(d["text"]) for d in self.docs], k1=0.9, b=0.4)
        self.emb = np.load(DATA / "emb.npy").astype(np.float32)
        self.enc = StaticModel.from_pretrained(str(DATA / "potion-base-8M"))
        self.tx_index = json.load(open(DATA / "tx_index.json"))
        self.samples = json.load(open(DATA / "samples.json"))
        self.lock = threading.Lock()

    # ---------------------------------------------------------------- retrieval
    def hybrid(self, q: str) -> list[int]:
        bm = np.argsort(-self.bm25.get_scores(tok(q)))[:POOL]
        qe = self.enc.encode([q])[0].astype(np.float32); qe /= np.linalg.norm(qe) + 1e-9
        dn = np.argsort(-(self.emb @ qe))[:POOL]
        s: dict[int, float] = {}
        for r in (bm, dn):
            for i, d in enumerate(r):
                s[int(d)] = s.get(int(d), 0.0) + 1.0 / (RRF_K + i + 1)
        return [d for d, _ in sorted(s.items(), key=lambda x: (-x[1], x[0]))][:POOL]

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
    def ask(self, question: str) -> dict:
        if not self.acct or not self.or_key:
            raise RuntimeError("missing OPENROUTER_API_KEY or ARC_PRIVATE_KEY in .env")
        question = question.strip()
        sample = next((s for s in self.samples if s["question"].lower() == question.lower()), None)
        qkey = sample["key"] if sample else "free:" + hashlib.sha256(question.encode()).hexdigest()[:16]
        with self.lock:
            t0 = time.perf_counter()
            pool = self.hybrid(question)
            t_chk = time.perf_counter()
            ok = self.sreg.functions.areVerified([self.docs[i]["hash"] for i in pool]).call()
            chk_ms = (time.perf_counter() - t_chk) * 1000
            kept = [i for i, o in zip(pool, ok) if o][:TOPK]
            unfiltered_top = pool[:TOPK]
            passages = [dict(id=f"P{n + 1}", text=self.docs[i]["text"], content_hash=self.docs[i]["hash"]) for n, i in enumerate(kept)]
            root = sha(b"".join(bytes.fromhex(p["content_hash"][2:]) for p in passages))
            qid = sha(qkey.encode())
            cts = now_iso()
            aid = sha(qid + cts.encode() + secrets.token_bytes(16))
            meta = f"Request metadata (audit only, do not mention): answer_id={hx(aid)}; question_id={hx(qid)}; client_ts={cts}"
            user = ("Answer the question using ONLY the numbered passages. Cite the passage ids you used.\n"
                    'Reply with JSON only: {"answer": "<short answer>", "citations": ["P1", ...]}. '
                    'If the passages do not contain the answer, reply {"answer": "I don\'t know", "citations": []}.\n\n'
                    + "\n\n".join(f"[{p['id']}] {p['text']}" for p in passages) + f"\n\nQuestion: {question}")
            body = {"model": MODEL, "temperature": 0, "max_tokens": 150,
                    "messages": [{"role": "system", "content": meta}, {"role": "user", "content": user}]}
            req = canonical(body)
            c = self._send(self.ilog.functions.commit(aid, qid, sha(req), root, sha(MODEL.encode()), len(passages)))
            r = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", req,
                                       {"Authorization": "Bearer " + self.or_key, "Content-Type": "application/json"})
            with urllib.request.urlopen(r, timeout=120) as resp:
                raw = resp.read()
            rts = now_iso()
            while self.w3.eth.block_number <= c["block"]:
                time.sleep(0.3)
            f = self._send(self.ilog.functions.finalize(aid, sha(raw)))
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
        bundle = dict(
            question=question, answer=mj.get("answer"), filter="verified-only", chain_id=CHAIN_ID, rpc=self.rpc_url,
            contract=SOURCE_REGISTRY, citations=cites,
            inference=dict(log_contract=INFERENCE_LOG, answer_id=hx(aid), question_key=qkey, question_id=hx(qid), model=MODEL,
                           client_request_ts=cts, client_response_received_ts=rts,
                           serialization='json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")',
                           request_body=req.decode("utf-8"), request_hash=hx(sha(req)),
                           passages=[dict(id=p["id"], content_hash=p["content_hash"]) for p in passages], passages_root=hx(root),
                           response_body_b64=base64.b64encode(raw).decode(), response_hash=hx(sha(raw)),
                           provider_generation_id=json.loads(raw).get("id"), provider_model=json.loads(raw).get("model"),
                           commit_tx=c["tx"], commit_block=c["block"], finalize_tx=f["tx"], finalize_block=f["block"],
                           explorer=dict(commit=f"{EXPLORER}/tx/{c['tx']}", finalize=f"{EXPLORER}/tx/{f['tx']}",
                                         log_contract=f"{EXPLORER}/address/{INFERENCE_LOG}")),
            how_to_verify="python verify_citations.py <this file>")
        stats = dict(pool=len(pool), verified_in_pool=sum(ok), unverified_filtered=len(pool) - sum(ok),
                     fakes_in_pool=sum(self.docs[i]["kind"] == "fake" for i in pool),
                     fakes_in_unfiltered_top5=sum(self.docs[i]["kind"] == "fake" for i in unfiltered_top),
                     onchain_check_ms=round(chk_ms), total_ms=round((time.perf_counter() - t0) * 1000),
                     gas_usdc=round(c["usdc"] + f["usdc"], 6), is_sample=bool(sample), gold=sample["gold"] if sample else None)
        return dict(bundle=bundle, stats=stats, passages=passages)
