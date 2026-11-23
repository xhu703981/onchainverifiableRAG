# Citation-bundle demo (Arc testnet)

- `answer.py <qid> [--naive]`: hybrid RAG over the fused top-50 → live `areVerified` filter (or `areRegistered` with `--naive`) → top-5 as [P1..P5] → gpt-4o-mini answers in JSON with cited ids → writes `bundle_<qid>.json`. `tx_index.json` maps each hash to its registration tx and is built by decoding the calldata of our registration txs over public RPC.
- Genuine: `bundle_test16.json` (Little Boy), `bundle_test19.json` (midpiece), `bundle_test62.json` (Lalo Schifrin). All PASS.
- `bundle_test62_tampered.json`: one character of the cited passage changed. FAIL, because the hash isn't registered.
- `bundle_test16_naive.json`: an unsafe service that only checks `isRegistered` answers "Big Man" citing 5 attacker-registered fakes. FAIL, because the registrant isn't trusted.
- `verifier_output.txt`: full verifier output for all five.

```bash
for f in experiments/demo/bundle_*.json; do python3 python/verify_citations.py "$f"; done
```

## Model-call log (InferenceLog 0xc7CfFb4Ae006AccC82813829CCC466B964aBCAE3)
- `answer.py <qid>` now commits the request hash on-chain, calls gpt-4o-mini with exactly those bytes, finalizes the response hash in a later block, and writes `bundle_<qid>_logged.json`. The previous version, without logging, is kept as `answer_v1_unlogged.py`.
- Genuine: `bundle_test16_logged.json`, `bundle_test62_logged.json`. Both pass all checks.
- Counterexamples, all FAIL: `bundle_test62_logged_fake_citation.json` (cites a passage that was not in the prompt), `..._prompt_tampered.json` (prompt edited after the commit), `..._answer_altered.json` (answer changed after the logged response).
- Full verifier output: `verifier_output_model_call.txt`. All on-chain txs: `../arc/inference_txs.json`.

## Corpus commitment counterexamples (CorpusRegistry v1)
- `bundle_corpus_hide.json`: a dishonest server dropped the best trusted candidate from the ranking, then committed everything consistently (real gpt-4o-mini call + on-chain log). Every hash check passes. Only **C5 replay** fails: "replay ranks 0xe400687334... at #6, but the answer's list omits it".
- `bundle_corpus_inject.json`: the server ranked a passage that is not in the committed corpus. **C3 inclusion** fails ("candidate #1 ... is not in the committed corpus"), and the replay differs too.
