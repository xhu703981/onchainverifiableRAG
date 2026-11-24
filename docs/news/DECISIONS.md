# Decision log, news registry phase 1

One row per decision. Times are UTC. Rows before 15:46Z (10:46 New York time) were written after the fact, when the pstack skills were adopted; their evidence still resolves.

| ts | phase | decision | why | evidence | result |
|---|---|---|---|---|---|
| 2026-11-24T15:10Z | contract | NewsRegistry keeps the owner and trustedPublishers pattern from SourceRegistry, adds `areTrustedRoots` | one call checks the whole retrieval scope | `contracts/NewsRegistry.sol` | 10 Foundry tests pass |
| 2026-11-24T15:10Z | contract | a trusted publisher may take over a root first registered by an untrusted address; everything else reverts on a duplicate root | manifests are pushed before the tx, so anyone could squat a root and leave it untrusted forever | `test/NewsRegistry.t.sol` test_trustedReclaimsSquattedRoot | pass |
| 2026-11-24T15:15Z | deploy | deployed from Python with eth_account, not `forge create --private-key` | keeps the key out of process arguments | `news/newsreg/deploy.py`, tx 0xabad7f18 | deployed, explorer-verified |
| 2026-11-24T15:20Z | feeds | picked 20 of 35 probed feeds by a fetch and extract test | Sky, The Hill, Politico and Axios return 403; CNN 500; CBC timeout; PBS 0 paragraphs | `news/sources.json` note | 20 feeds |
| 2026-11-24T15:25Z | fetch | lxml extraction runs in one process, never in threads | a threaded probe segfaulted | probe log, `newsreg/crawl.py` | stable |
| 2026-11-24T15:31Z | fetch | decode gzip bodies that servers send unasked | UN News feed came back gzipped and parsed as 0 entries | `newsreg/fetcher.py` | UN News 12/12 ok |
| 2026-11-24T15:32Z | manifests | publish manifests on a separate `news-manifests` branch through a git worktree | the 8-hourly job must not push to main or touch the working tree | branch news-manifests | batch 1 manifest live |
| 2026-11-24T15:32Z | register | first batch registered right away, then every 8 h | something visible on the explorer now | tx 0xd765fb53 | 220 articles |
| 2026-11-24T15:35Z | rag | bind the retrieval scope through the hashed request (system line), keep `passagesRoot` as before | InferenceLog and the M checks stay unchanged | `newsreg/engine.py` meta line, check S4 | genuine bundle passes |
| 2026-11-24T15:36Z | rag | relevance gate: query-token coverage >= 0.4 and cosine >= 0.35 | tuned on 8 questions; tungsten and sourdough fall out, real news questions pass | `retrieval_news_v1.json` | out-of-corpus abstains |
| 2026-11-24T15:38Z | rag | prompt example uses "[P2]." with no space after the marker | the M3 parser counted "[P2] or" in the instructions as a passage | first bundle failed M3 with 7 vs 6 passages | fixed, M3 passes |
| 2026-11-24T15:40Z | tests | untrusted FAIL case uses a real batch registered on-chain by a throwaway key | proves the trust check, not only a missing record | tx 0xe5c99fb4, `news/examples/untrusted_batch.json` | FAIL for the right check |
| 2026-11-24T15:43Z | extraction | did not change extraction rules after the spot-check | a rule change is a new NORMALIZATION.md version under already registered batches | `news/SPOTCHECK.md` | 7/30 one-line residue reported |
| 2026-11-24T15:46Z | start | adopted pstack skills: architect, tdd, interrogate, unslop, show-me-your-work | parent request | /workspace/vendor-skills/plugins/pstack | rows below are live |
| 2026-11-24T15:47Z | interrogate | verifier time parse used local mktime | probe: July timestamp off by 3600 s under America/New_York | test_verifier_time_parse_is_utc_in_any_local_timezone failed first | fixed with calendar.timegm, passes |
| 2026-11-24T15:48Z | interrogate | removed paragraph and article index fields from bundles | probe: index 99 still passed, the verifier never checked it | `interrogate/probes.py` P1, test_bundle_carries_no_unverified_position_claims | removed, passes |
| 2026-11-24T15:48Z | interrogate | JCS prints integral floats as integers in Python too | probe: version 1.0 passed in the browser and crashed in Python | test_jcs_integral_float_is_serialized_like_ecmascript failed first | consistent, passes |
| 2026-11-24T15:48Z | interrogate | kept duplicated scope roots as harmless | the scope hash de-duplicates, so the set is the same | probe P4 | dropped |
| 2026-11-24T15:49Z | register | made the registration job idempotent: build records the batch and assigns articles in one transaction, then every run finishes built batches, and skips the tx if our key already registered the root | a crash after a mined tx would have registered the same articles twice | `tests/test_register_idempotent.py` failed first (3 tests) | passes; `ArcChain.lookup` read batch 1 back from Arc |
| 2026-11-24T15:50Z | copy | rewrote news README, the root README section and page copy with unslop rules | dashes, arrows and filler | `news/README.md`, `webdemo/static/news.html` | screenshot updated |
