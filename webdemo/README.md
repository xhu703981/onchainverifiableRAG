# Verifiable RAG: local web demo (Arc testnet 5042002 only)

A single page that answers using **only passages registered on Arc testnet by a trusted publisher**, logs every model call on-chain, and lets you **verify the answer yourself**.

## Run (Windows PowerShell, in this folder)
    powershell -ExecutionPolicy Bypass -File .\start.ps1        # -> http://127.0.0.1:8765
    powershell -ExecutionPolicy Bypass -File .\stop.ps1
Foreground alternative: `.\.venv\Scripts\python.exe app.py --port 8765`. Logs: server.log / server.err.log.

First-time setup (already done): `py -3.11 -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt`, plus a `.env` (see `.env.example`, gitignored).

## What happens when you ask
1. Hybrid retrieval (`retrieval_v1.py`) over the **committed corpus**: 30 PoisonedRAG NQ questions, 1,498 real passages + 500 attacker fakes = 1,998 passages. BM25 + dense embeddings (model2vec potion-base-8M), RRF fusion, top-50 ranked candidates.
2. One live `SourceRegistry.areVerified` call on Arc drops every candidate that is not registered by a trusted publisher. The attacker's fakes are registered on-chain too, but by an untrusted wallet, so they are dropped. The page shows how many were filtered.
3. The top-5 verified passages go into a prompt. The prompt's metadata line carries `corpus=v1:<root>`, `retrieval_config=<hash>` and `retrieval=sha256(ranked candidate list)`. It also carries a `nonce`, the client time and `arc_block=<number>:<hash>`, the latest Arc block fetched before retrieval.
4. openai/gpt-4o-mini (via OpenRouter) answers. Then **one** tx, `AnswerRegistry.record(root)`, stores the answer root: a Merkle root (same SHA-256 tree as the corpus) over 14 fixed, labeled leaves, `x = sha256(label || 0x00 || value)`, in this order: `corpus_root, retrieval_config, anchor_block, question, ranked, t_retrieval*, request, t_request*, model, served_model, provider, response, t_response*, answer` (* self-reported times; `answer` = sha256 of the answer plus each cited id and text hash). The bundle carries the leaves; the verifier recomputes every one from the bundle (A1–A3), so any edit changes the root, and the anchor block hash plus the record block give a "between block A and B" window. `vc.answer_leaf_proof` gives a short Merkle path for checking a single leaf. Bundles from the earlier two-tx `InferenceLog` flow still verify (M1–M6).

## Corpus commitment (CorpusRegistry 0x1Fb5aDDb3CdB5DE1945f3Ab32c7FF0b6d2CDA2de, v1)
- **Merkle tree**: SHA-256, domain separated. leaf = sha256(0x00 ‖ passageHash), where passageHash = sha256(UTF-8 text). node = sha256(0x01 ‖ left ‖ right). Leaves are sorted by passageHash, and an odd node at the end of a level is promoted unchanged. Proofs are `{leaf_index, path}`, and the leaf count comes from the chain. 1,998 leaves, ≤ 11 siblings per proof.
- **All 1,998 passages are committed, fakes included**: the corpus is exactly what the retriever searches. The trust filter runs after retrieval, and trust lives in SourceRegistry.
- **configHash** = sha256(`data/retrieval_config_v1.json`). That file pins sha256 of the corpus file (`data/corpus_v1.jsonl`, leaf order), the retriever code (`retrieval_v1.py`), the model weights and tokenizer, and every parameter (BM25 k1/b/stopwords, WordPiece rules, RRF k, depth 50, prompt_k 5). Rebuild with `python build_corpus_v1.py`.
- **URI** on-chain: the config's raw GitHub URL at an immutable commit (`636dfc3`). Artifact paths resolve relative to it, and every file is sha256-checked against the config, so the host is untrusted.
- **Determinism**: everything is float64. Orderings use scores quantised to 1e-9 with ties broken by leaf index. The JS port (`static/replay.js`) reproduces the Python reference exactly: same ids, ranks and RRF scores on 420 test queries, and the WordPiece tokenizer is identical to HF `tokenizers` on all 1,998 passages.

## Verify
- **Verify** button: runs the `verify_citations.py` checks **in your browser** (`static/arcverify.js`, SHA-256 via WebCrypto, keccak via vendored @noble/hashes 1.8.0), calling the public Arc RPC directly. It makes zero calls to this server. Click any node to see the raw RPC request and response, the hash recomputed locally next to the on-chain value, and a `cast` command that reproduces it. Source checks: the hash is recomputed from the text, the CID matches, the source is registered, the publisher is trusted, and the registration tx contains the hash. Model-call checks M1–M6: the request, passages and response hashes match what was committed, with commit before finalize. **Corpus checks C1–C5** (the *Corpus* node):
  - C1: on-chain root, leaf count and config hash match, published by an authorized publisher.
  - C2: the config hash and the ranked-list hash sit inside the hashed request.
  - C3: every ranked candidate has a valid inclusion proof.
  - C4: the prompt is exactly the first 5 *verified* candidates, so none was skipped.
  - C5: **replay**. The page fetches the committed corpus, weights, tokenizer and code (about 31 MB, once per page, sha256-checked), rebuilds the index in the browser (about 0.4 s) and requires the identical ranked list. CLI: `python verify_citations.py bundle.json` (needs numpy for C5; `--artifacts data` uses local copies, `--no-replay` skips it).
- **Tamper demo**: changes one character of a cited passage, the answer, the logged prompt, the served model, or **hides a document** from the ranking, in a *copy* of the bundle, then verifies. The copy fails.
- **Dishonest-server counterexamples** (real calls, committed consistently): `experiments/demo/bundle_corpus_hide.json` dropped the best trusted candidate. Every hash matches, and only the replay fails. `bundle_corpus_inject.json` ranks a passage that is not in the corpus, so its inclusion proof fails.
- **Download bundle**, then verify independently without this server (standard library only):
      python verify_citations.py bundle_xxxx.json            # exit code 0 = all checks passed
      python verify_citations.py bundle_xxxx.json --trust 0xYourTrustedPublisher
- **Explorer**: open the commit/finalize/registration links (https://explorer.testnet.arc.io). Use "Decode input" to see that the hashes match the bundle.

Trust gap: the log proves *which* exact request/response bytes existed and when. It does not prove that OpenRouter actually ran the model on them. Reasoning is not verified. The replay proves the ranking follows from the committed corpus and retriever, not that the corpus is complete. C4 uses the *current* allowlist.

Contracts: SourceRegistry 0x9350E7E458abe568B40c1Fc28c66dE674865878e · AnswerRegistry 0x580D6da6594b81e7631Ed8B5E46ae06F5C1617e0 · InferenceLog (earlier bundles) 0xc7CfFb4Ae006AccC82813829CCC466B964aBCAE3 · CorpusRegistry 0x1Fb5aDDb3CdB5DE1945f3Ab32c7FF0b6d2CDA2de.
Cost per question: about 0.0015 testnet USDC of gas (one `record` tx) plus about $0.0001 for gpt-4o-mini.

No-cost preview of the saved example: http://127.0.0.1:8765/?demo=verify  (or ?demo=answer, ?demo=tamper, ?demo=model, ?demo=hide, ?demo=corpus)

Standalone verifier: `verify.html`, rebuilt with `python build_verify_html.py`. It is one inlined file, works from file://, and its CSP allows network access only to rpc.testnet.arc.io and raw.githubusercontent.com (replay files, hash-checked). Drag a bundle onto any page, or use "verify a file".
Parity with the Python verifier: `python parity/compare.py` (Node) and `node parity/browser_test.js` + `python parity/compare_browser.py` (headless Chrome).
