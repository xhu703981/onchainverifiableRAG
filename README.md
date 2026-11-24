# Onchain Verifiable-Source RAG

**Situation.** RAG answers can hallucinate, and an attacker can poison the corpus with fake passages ([PoisonedRAG](https://arxiv.org/abs/2402.07867)). A user who doesn't trust the service has no way to check where an answer came from.

**Task.** Make the model answer only from trusted sources, and let anyone check, without trusting us, that the cited passages are genuine and were actually in the prompt.

**Action.**
- Trusted documents are hashed (SHA-256) and registered on **Arc testnet** in `SourceRegistry`. A passage counts as *verified* only if it is registered **and** its registrant is on a trusted-publisher allowlist.
- Retrieval (hybrid BM25 + dense) keeps only verified passages, checked live on-chain.
- **Each answer is one 32-byte hash on Arc** (`AnswerRegistry`, one tx): a Merkle root over 14 labeled leaves (corpus, retrieval config, question, ranking, prompt, requested/served model, provider, response, answer, step times, and an Arc block hash fetched before retrieval). Changing any piece changes the root, and the answer is pinned between that block and the record block.
- Anyone can verify an answer bundle **in their own browser**, directly against the public Arc RPC (zero calls to our server): the citation hashes, their registration and trusted publisher, that the cited text was in the committed prompt, and that the answer matches the logged response.
- The whole corpus (all 1,998 passages the retriever searches) is committed as a Merkle root in `CorpusRegistry`, together with the hash of the exact retriever (code, embedding weights, parameters). Each answer binds its full ranked candidate list into the committed prompt. The verifier checks an inclusion proof for every candidate, then **replays retrieval in the browser** over the committed corpus and requires the identical ranking. A document hidden from the ranking shows up red.
- The verifier also checks that the requested model matches its committed hash. It reads the served model and provider from the hashed response, so silent routing to another model shows up as a red check.

```mermaid
flowchart LR
  K[(Corpus root<br/>on Arc)] --> R
  Q[Question] --> R[Hybrid retrieval] --> F{On-chain filter<br/>registered + trusted?}
  F -- no --> X[Dropped]
  F -- yes --> M[LLM] --> Z[One answer root<br/>on Arc] --> A[Answer + citation bundle]
  A --> V[Anyone verifies on Arc]
```

**Result** (30 PoisonedRAG NQ questions, gpt-4o-mini):

| | hallucination | fakes reaching the model |
|---|---|---|
| poisoned corpus, no filter | 0.90 | 4.97 / 5 |
| naive "is registered" filter (attacker registers its own fakes) | 0.90 | 155 / 155 pass |
| **verified-only (trusted publishers)** | **0.13** | **0 / 155 pass** |

- The on-chain filter takes about 180 ms per question (50 candidates, one call).
- Tampered bundles (an edited passage, an edited prompt, an altered answer, a fake citation) all fail verification.
- A dishonest server that drops the best trusted document *and commits consistently* passes every hash check, but fails the replay. A passage that isn't in the corpus fails its inclusion proof.

![verify with on-chain evidence](webdemo/screens/readme_evidence.png)

**Contracts (Arc testnet, chain 5042002)**
- SourceRegistry: [`0x9350E7E458abe568B40c1Fc28c66dE674865878e`](https://explorer.testnet.arc.io/address/0x9350E7E458abe568B40c1Fc28c66dE674865878e)
- AnswerRegistry: [`0x580D6da6594b81e7631Ed8B5E46ae06F5C1617e0`](https://explorer.testnet.arc.io/address/0x580D6da6594b81e7631Ed8B5E46ae06F5C1617e0) (one root per answer)
- InferenceLog: [`0xc7CfFb4Ae006AccC82813829CCC466B964aBCAE3`](https://explorer.testnet.arc.io/address/0xc7CfFb4Ae006AccC82813829CCC466B964aBCAE3) (earlier two-tx bundles, still verifiable)
- CorpusRegistry: [`0x1Fb5aDDb3CdB5DE1945f3Ab32c7FF0b6d2CDA2de`](https://explorer.testnet.arc.io/address/0x1Fb5aDDb3CdB5DE1945f3Ab32c7FF0b6d2CDA2de) (corpus v1, root `0x949fe997…ed81593`)

**Verify it yourself.** Open [`verify.html`](verify.html), a single local file that needs no server and talks only to `rpc.testnet.arc.io` (plus the hash-checked corpus and model files on GitHub for the replay), and drop in a bundle. Every check shows the raw RPC request and response, plus a `cast` command that reproduces it.

**Limitation.** The log proves which exact prompt and response existed and when, and that the cited sources are genuine. It does not prove that the provider actually ran the model on that prompt, and it does not verify the model's reasoning. The served model and provider are self-reported by the provider, not cryptographically proven. With one tx per answer, the root proves *when* (between two blocks) but no longer that the prompt was fixed on-chain before the model replied; the step times inside it are self-reported. The replay proves the ranking follows from the committed corpus and retriever. It doesn't prove the corpus itself is complete or unbiased, and the trust filter is re-checked against the current allowlist.
