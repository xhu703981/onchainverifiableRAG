# Verifiable RAG: local web demo (Arc testnet 5042002 only)

A single page that answers using **only passages registered on Arc testnet by a trusted publisher**, logs every model call on-chain, and lets you **verify the answer yourself**.

## Run (Windows PowerShell, in this folder)
    powershell -ExecutionPolicy Bypass -File .\start.ps1        # -> http://127.0.0.1:8765
    powershell -ExecutionPolicy Bypass -File .\stop.ps1
Foreground alternative: `.\.venv\Scripts\python.exe app.py --port 8765`. Logs: server.log / server.err.log.

First-time setup (already done): `py -3.11 -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt`, plus a `.env` (see `.env.example`, gitignored).

## What happens when you ask
1. Hybrid retrieval over a small demo corpus (30 PoisonedRAG NQ questions: 1,498 real passages + 500 attacker fakes): BM25 + dense embeddings (model2vec potion-base-8M), RRF fusion, top-50 candidates.
2. One live `SourceRegistry.areVerified` call on Arc drops every candidate that is not registered by a trusted publisher. The attacker's fakes are registered on-chain too, but by an untrusted wallet, so they are dropped. The page shows how many were filtered.
3. The top-5 verified passages go into a prompt. `InferenceLog.commit` stores sha256(exact request bytes) plus a root over the passage hashes **before** the call.
4. openai/gpt-4o-mini (via OpenRouter) answers. `InferenceLog.finalize` stores sha256(raw response bytes) in a later block.

## Verify
- **Verify** button: runs the `verify_citations.py` checks **in your browser** (`static/arcverify.js`, SHA-256 via WebCrypto, keccak via vendored @noble/hashes 1.8.0), calling the public Arc RPC directly. It makes zero calls to this server. Click any node to see the raw RPC request and response, the hash recomputed locally next to the on-chain value, and a `cast` command that reproduces it. Source checks: the hash is recomputed from the text, the CID matches, the source is registered, the publisher is trusted, and the registration tx contains the hash. Model-call checks M1–M5: the request, passages and response hashes match what was committed, with commit before finalize.
- **Tamper demo**: changes one character of a cited passage, the answer, or the logged prompt in a *copy* of the bundle, then verifies. The copy fails.
- **Download bundle**, then verify independently without this server (standard library only):
      python verify_citations.py bundle_xxxx.json            # exit code 0 = all checks passed
      python verify_citations.py bundle_xxxx.json --trust 0xYourTrustedPublisher
- **Explorer**: open the commit/finalize/registration links (https://explorer.testnet.arc.io). Use "Decode input" to see that the hashes match the bundle.

Trust gap: the log proves *which* exact request/response bytes existed and when. It does not prove that OpenRouter actually ran the model on them. Reasoning is not verified.

Contracts: SourceRegistry 0x9350E7E458abe568B40c1Fc28c66dE674865878e · InferenceLog 0xc7CfFb4Ae006AccC82813829CCC466B964aBCAE3.
Cost per question: about 0.005 testnet USDC of gas (commit + finalize) plus about $0.0001 for gpt-4o-mini.

No-cost preview of the saved example: http://127.0.0.1:8765/?demo=verify  (or ?demo=answer, ?demo=tamper, ?demo=model)

Standalone verifier: `verify.html`, rebuilt with `python build_verify_html.py`. It is one inlined file, works from file://, and its CSP allows network access only to rpc.testnet.arc.io. Drag a bundle onto any page, or use "verify a file".
Parity with the Python verifier: `python parity/compare.py` (Node) and `node parity/browser_test.js` (headless Chrome).
