# Onchain Verifiable Source RAG

Register content hashes of RAG source files on an Ethereum-compatible chain, then filter retrieved chunks so only on-chain-verified sources are used.

The Solidity contract `SourceRegistry` stores a SHA-256 content hash (`bytes32`) together with a CID, a source URI, the registrant, and a timestamp. Python tooling hashes files or text the same way, registers new hashes, and drops unverified chunks before they enter a RAG pipeline.

This project is intended for local development and tests. Do not deploy to mainnet from these scripts unless you explicitly choose to do so yourself.

## Repository layout

- `contracts/SourceRegistry.sol` — registry contract
- `test/SourceRegistry.t.sol` — Foundry tests
- `script/Deploy.s.sol` — Foundry deploy script (broadcast only if you run it)
- `python/hashing.py` — shared SHA-256 helpers (bytes, UTF-8 text, streamed files)
- `python/verify.py` — hash a file and query `isRegistered` / `getSource`
- `python/register.py` — batch-register files under a folder
- `python/retriever.py` — `VerifiedRetriever` filters chunks by on-chain registration
- `python/tests/` — pytest suite (hash vectors and retriever logic; no network)

## Contract: build and test

Requires [Foundry](https://book.getfoundry.sh/getting-started/installation).

```bash
forge build
forge test -vv
```

Configuration is in `foundry.toml` (`src = "contracts"`, Solidity 0.8.24).

To deploy to a local chain you control (for example Anvil), you can use:

```bash
forge script script/Deploy.s.sol --rpc-url "$RPC_URL" --broadcast --private-key "$PRIVATE_KEY"
```

Do not point this at mainnet.

## Python setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run the unit tests (offline; the retriever uses `unittest.mock`):

```bash
pytest
```

## Environment variables

| Variable | Used by | Purpose |
|---|---|---|
| `RPC_URL` | `verify.py`, `register.py` | JSON-RPC endpoint (e.g. `http://127.0.0.1:8545`) |
| `REGISTRY_ADDRESS` | `verify.py`, `register.py` | Deployed `SourceRegistry` address |
| `PRIVATE_KEY` | `register.py` (not required with `--dry-run`) | Hex private key that signs `registerSource` transactions |

The ABI is loaded from `out/SourceRegistry.sol/SourceRegistry.json` after `forge build`. If that artifact is missing, a minimal embedded ABI is used instead.

## `python/verify.py`

Compute the SHA-256 of a file as a `bytes32` / `0x`-prefixed hex string, then call `isRegistered` and `getSource` on the registry.

```bash
export RPC_URL=http://127.0.0.1:8545
export REGISTRY_ADDRESS=0xYourRegistryAddress

python python/verify.py /path/to/file.txt
```

Example output:

```
file: /path/to/file.txt
contentHash: 0xba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad
isRegistered: true
exists: true
cid: placeholder-cid-ba7816bf
sourceUri: file:///path/to/file.txt
registrant: 0x...
timestamp: 1700000000
```

## `python/register.py`

Walk a folder recursively, hash each file, skip hashes that are already registered, and call `registerSource(hash, cid, sourceUri)`.

- `cid` is a placeholder: `placeholder-cid-<first 8 hex chars of the hash>`
- `sourceUri` is the file's absolute `file://` URI
- Directories such as `.git`, `.venv`, `__pycache__`, `out`, `cache`, and `lib` are skipped

```bash
export RPC_URL=http://127.0.0.1:8545
export REGISTRY_ADDRESS=0xYourRegistryAddress
export PRIVATE_KEY=0xYourPrivateKey

# Preview actions without sending transactions
python python/register.py --dry-run /path/to/corpus

# Submit transactions
python python/register.py /path/to/corpus
```

`--dry-run` still queries the chain to detect already-registered hashes, but it never signs or sends a transaction and does not require `PRIVATE_KEY`.

## `python/retriever.py`

`VerifiedRetriever` hashes each chunk (a string, or a dict with a `text` field) as UTF-8 SHA-256 and keeps only chunks whose hash is registered.

```python
from retriever import VerifiedRetriever

# `registry` can be a web3.py contract or any object with isRegistered(hash) -> bool
retriever = VerifiedRetriever(registry)

chunks = [
    "trusted source paragraph",
    {"text": "another passage", "id": 2},
    "unregistered text",
]
verified = retriever.filter(chunks)
```

The retriever talks to the registry only through `isRegistered`. It does not send transactions.


## Trust model (v2 contract)

Registration is permissionless, so `isRegistered` alone is **not** a defense: anyone can register poisoned passages.
The v2 contract adds an owner-managed allowlist of trusted publishers:

- `owner`, `transferOwnership`, `addTrustedPublisher`, `removeTrustedPublisher`, `isTrustedPublisher`
- `isVerified(hash)`: registered **and** the registrant is currently trusted. `areVerified(bytes32[])` and `areRegistered(bytes32[])` are batch views (one RPC call per retrieval).
- `registerBatch(bytes32[])`: one storage slot per hash (registrant + timestamp), about 26k gas per hash vs about 250k for `registerSource`. Already-registered hashes are skipped. No CID string is stored: `cidOf(hash)` derives the CIDv1 (raw, sha2-256, base32) on-chain, which equals `ipfs add --only-hash --cid-version 1` for files of 256 KiB or less. `getSource` returns it for batch records.
- Existing `registerSource` / `updateSource` / `getSource` / `totalSources` behave as before (`totalSources` still counts only `registerSource` entries; `totalAllSources` counts both). `registerSource` now also rejects a hash that was batch-registered.

Why not a Merkle root? One tx could commit millions of passages, but every chunk would then need a proof served next to it, you can't look up a single hash on-chain, and revoking one passage means re-committing the whole root. At experiment scale (≤ 10k passages ≈ 5–6 testnet USDC), per-hash batch storage keeps verification a plain `eth_call`.

Python: `VerifiedRetriever(contract)` filters on `areVerified` by default (`mode="registered"` reproduces the naive filter). `register.py --batch N` uses `registerBatch`. `verify.py` prints `isVerified`, the registrant's trust and the local vs on-chain CID. Set `EXPECTED_CHAIN_ID=5042002` to refuse any other chain.

## Arc testnet deployment
- SourceRegistry v2: `0x9350E7E458abe568B40c1Fc28c66dE674865878e` (chain 5042002), source verified on Blockscout: https://explorer.testnet.arc.io/address/0x9350E7E458abe568B40c1Fc28c66dE674865878e
- Tooling: Circle arc-foundry v0.8.0-4 (`arc-forge test --network arc`), regular Foundry for local tests.
- Experiments: see `experiments/results.md`.

## How to verify an answer (you don't need to trust us)

Every answer comes with a **citation bundle** (JSON; see `experiments/demo/bundle_*.json`): the answer plus, for each cited passage, its exact text, SHA-256 `content_hash`, CIDv1, registrant, registration tx hash, block number, contract address, chain id 5042002 and explorer links.

### Option A: one command (Python standard library only, public RPC)
```bash
python3 python/verify_citations.py experiments/demo/bundle_test62.json
# optional: use YOUR OWN list of trusted publishers instead of the contract's allowlist
python3 python/verify_citations.py bundle.json --trust 0xPublisherYouTrust ...
```
For each citation the verifier recomputes and checks the following, and prints PASS or FAIL with reasons (exit code 0 only if everything passes):
1. sha256(text) equals the hash in the bundle. Any edited character fails.
2. The CID recomputed from that hash matches the bundle's CID.
3. `getSource(hash)` on Arc testnet says the hash is registered, and the registrant matches the bundle.
4. The registrant is a trusted publisher (`isTrustedPublisher`, or your own `--trust` list).
5. The cited tx was sent to this contract by that registrant, succeeded, has the hash in its `registerBatch` calldata, emitted `BatchRegistered`, and its block time equals the on-chain registration time.

It also checks that the RPC is on chain 5042002 and that the address holds contract code. It talks only to `https://rpc.testnet.arc.io` (or any `--rpc` you choose), not to our servers.

### Option B: by hand in the block explorer (no code)
1. **Hash the passage text.** Copy the citation's `text` exactly and get its SHA-256, e.g. save it to `p.txt` with no trailing newline and run `shasum -a 256 p.txt` (macOS/Linux) or `certutil -hashfile p.txt SHA256` (Windows). Put `0x` in front. It must equal `content_hash`. If even one character differs, the hash is completely different.
2. **Open the contract.** Go to https://explorer.testnet.arc.io/address/0x9350E7E458abe568B40c1Fc28c66dE674865878e. The source code is verified there, so you can read what it does. Open **Contract → Read contract**.
3. **Call `isVerified`** with `contentHash` = your 0x-hash. `true` means the text was registered by a publisher on the trusted list.
4. **Call `getSource`** with the same hash to see `exists`, `cid`, `registrant` and `timestamp`. Then call **`isTrustedPublisher`** with that `registrant` and check **`owner`** (who manages the trust list). If you don't trust that owner, decide yourself whether you trust the registrant address.
5. **Optional, check the tx:** open the bundle's `explorer.tx` link. The tx should be "To" this contract, "From" the registrant, status Success, method `registerBatch`, with your hash among the input values; its timestamp equals `getSource().timestamp`.
6. **Optional, check the CID:** `ipfs add --only-hash --cid-version 1 p.txt` prints the same `bafkrei…` CID.

What a PASS means: the exact text you were shown was published on-chain, at that time, by an address on the trust list. It does **not** prove the publisher's content is true, only who vouched for it and that it hasn't changed since.

## Verify the model call (InferenceLog)

`contracts/InferenceLog.sol` is deployed at `0xc7CfFb4Ae006AccC82813829CCC466B964aBCAE3` on Arc testnet, with its source verified at https://explorer.testnet.arc.io/address/0xc7CfFb4Ae006AccC82813829CCC466B964aBCAE3. It records each answer in two write-once steps (hashes only). Only loggers set by the owner can write; the deployer is the default logger.

1. **`commit(answerId, questionId, requestHash, passagesRoot, modelHash, passageCount)`** is sent *before* the model call.
   - `requestHash` = sha256 of the exact HTTP body sent to OpenRouter. The body is serialized canonically: `json.dumps(body, sort_keys=True, separators=(",",":"), ensure_ascii=False)` encoded as UTF-8. It contains the model, the parameters and the full prompt with the `[P1]..[P5]` passages, plus a system line carrying `answer_id`, `question_id` and the client timestamp.
   - `passagesRoot` = sha256 of the concatenated 32-byte sha256 digests of P1..Pn, in prompt order.
2. **`finalize(answerId, responseHash)`** is sent *after* the call, and the contract only accepts it in a later block. `responseHash` = sha256 of the exact raw response bytes.

Cost on Arc testnet: about 175–190k gas to commit and 58k to finalize, roughly 0.005 USDC per answer.

The bundle (`experiments/demo/bundle_*_logged.json`) carries the exact request body, the raw response (base64), the passage list and both tx hashes. `python3 python/verify_citations.py bundle.json` then also runs checks M1–M5:
- **M1:** the record exists, was written by an authorized logger, and is finalized.
- **M2:** the request bytes hash to the on-chain `requestHash`, and the model, ids and timestamp inside them match.
- **M3:** every prompt passage hashes into `passagesRoot`, and every cited text appears verbatim in the prompt under its id.
- **M4:** the raw response hashes to `responseHash`, and the bundle's answer and citation ids are exactly what that response says.
- **M5:** the commit is in an earlier block than the finalize, and both txs are the real log txs for this `answerId`.

By hand: in the explorer's **Contract → Read contract** on the InferenceLog address, call `getRecord(answerId)`. Then compare `requestHash` and `responseHash` with the SHA-256 of the bundle's `request_body` and of the decoded `response_body_b64`.

**Design choices.**
- *Commit, then finalize:* it costs a second tx, about 1 s more latency and about 0.0012 USDC. In return it proves the exact prompt was fixed on-chain before the answer existed, and a later block number orders the two steps. A single-tx log would be cheaper, but it would only show both were recorded together.
- *Hashes, not text:* this keeps gas low and keeps documents private. The price is that the verifier needs the bundle; the chain alone can't show the prompt.
- *sha256 rather than keccak:* anyone can recompute it with stock tools.

**What this does NOT prove (the trust gap).** The service itself computes and signs every hash. A dishonest service could commit a request it never sent and then finalize a response it made up, and every check would still pass. On-chain logging makes the service's claims tamper-evident and time-ordered: it can't change the prompt, passages or answer after the fact, and it can't backdate them. It does not prove the model provider actually produced the response. Closing that gap needs the provider to sign responses (signed attestations of request and response hashes), or inference running in a TEE with remote attestation, or zkML / opML-style proofs. None of these are available through OpenRouter today. The model's reasoning is not verified at all.
