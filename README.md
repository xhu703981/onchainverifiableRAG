# Onchain Verifiable-Source RAG

**Situation.** RAG answers can hallucinate, and an attacker can poison the corpus with fake passages ([PoisonedRAG](https://arxiv.org/abs/2402.07867)). A user who doesn't trust the service has no way to check where an answer came from.

**Task.** Make the model answer only from trusted sources, and let anyone check, without trusting us, that the cited passages are genuine and were actually in the prompt.

**Action.**
- Trusted documents are hashed (SHA-256) and registered on **Arc testnet** in `SourceRegistry`. A passage counts as *verified* only if it is registered **and** its registrant is on a trusted-publisher allowlist.
- Retrieval (hybrid BM25 + dense) keeps only verified passages, checked live on-chain.
- Each model call goes into `InferenceLog`. The hash of the exact prompt bytes and of the passage set is committed *before* the call. The hash of the exact response bytes is finalized *after* it, in a later block.
- Anyone can verify an answer bundle **in their own browser**, directly against the public Arc RPC (zero calls to our server): the citation hashes, their registration and trusted publisher, that the cited text was in the committed prompt, and that the answer matches the logged response.
- The verifier also checks that the requested model matches its committed hash. It reads the served model and provider from the hashed response, so silent routing to another model shows up as a red check.

```mermaid
flowchart LR
  Q[Question] --> R[Hybrid retrieval] --> F{On-chain filter<br/>registered + trusted?}
  F -- no --> X[Dropped]
  F -- yes --> C[Commit prompt hash] --> M[LLM] --> Z[Finalize response hash] --> A[Answer + citation bundle]
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

![verify](webdemo/screens/verify.png)

**Contracts (Arc testnet, chain 5042002)**
- SourceRegistry: [`0x9350E7E458abe568B40c1Fc28c66dE674865878e`](https://explorer.testnet.arc.io/address/0x9350E7E458abe568B40c1Fc28c66dE674865878e)
- InferenceLog: [`0xc7CfFb4Ae006AccC82813829CCC466B964aBCAE3`](https://explorer.testnet.arc.io/address/0xc7CfFb4Ae006AccC82813829CCC466B964aBCAE3)

**Verify it yourself.** Open [`verify.html`](verify.html), a single local file that needs no server and talks only to `rpc.testnet.arc.io`, and drop in a bundle. Every check shows the raw RPC request and response, plus a `cast` command that reproduces it.

**Limitation.** The log proves which exact prompt and response existed and when, and that the cited sources are genuine. It does not prove that the provider actually ran the model on that prompt, and it does not verify the model's reasoning. The served model and provider are self-reported by the provider, not cryptographically proven.
