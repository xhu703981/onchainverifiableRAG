# Verifiable news registry (phase 1)

We verify that an AI answer's information comes from **news sources you choose to trust**, word for word.
We do not verify that the news is true.

**Pipeline.** 20 English RSS feeds (`sources.json`) → hourly fetch (`Fetcher` interface; `HttpFetcher` = trust level L0, a zkTLS
fetcher can replace it) → raw HTML saved → trafilatura extraction + normalization (`NORMALIZATION.md`, environment pinned in
`requirements.lock`) → paragraphs → Merkle (`news-merkle-v1`) → every 8 h one batch: manifest published on the
[`news-manifests`](https://github.com/xhu703981/onchainverifiableRAG/tree/news-manifests/manifests) branch, root + manifest hash
registered in **NewsRegistry** on Arc testnet. Article text stays in the local DB, never on-chain or in manifests.

```
paragraph leaf = sha256(0x00 ‖ text)          node = sha256(0x01 ‖ L ‖ R), odd node promoted, 1 leaf: root = leaf
article leaf   = sha256(0x02 ‖ sha256(JCS(meta)) ‖ article_root)
meta = {url (tracking params stripped), outlet, title, published_at, fetched_at, extractor_version, normalization, raw_html_sha256, version}
batch root     = Merkle(article leaves)  →  NewsRegistry.registerBatch(root, manifestHash, articleCount, manifestURI)
```

**Answering.** Retrieval (the committed BM25 + dense retriever, `retrieval_news_v1.json`) runs only over paragraphs in batches
that are `isTrustedRoot` right now. If nothing passes the relevance threshold, or the question names a date after the cutoff,
the answer is `Not found in registered news.` with no model call. Otherwise the prompt holds the paragraphs as [P1..Pn], and
the request, whose system line binds the scope hash (sorted batch roots), the config hash and `as_of`, is committed to
InferenceLog before the call (same contract and flow as the main demo). Every sentence must cite; uncited sentences show as
*unverified*. `as_of` = block time of the latest batch in scope: a proven "existed no later than" time. `published_at` and
`fetched_at` are claims (feed / our crawler).

**Verify.** `python webdemo/verify_citations.py bundle.json` (dispatches to `verify_news.py`; add `--trust 0x…` to use your own
trusted keys, `--outlets "BBC News" …` to accept only sources you chose), or the page at `news/app.py` (`newsverify.js`, checks
run in your browser against the Arc RPC). Text → paragraph proof → article root → article leaf with meta → batch root → on-chain
`isTrustedRoot` → registration tx → published manifest, plus the unchanged M checks for the model call.

**Trust semantics.** `isTrustedRoot` reads the *current* allowlist: revoking a key un-trusts every batch it ever registered.
Registration is permissionless; a trusted publisher may take over a root first registered by an untrusted address, so nobody can
squat a root seen in a manifest or the mempool.

**Not proven / limitations.** Truth. Completeness: full text is not redistributed (copyright), so nobody can replay retrieval;
an article that was left out (for example one that contradicts the answer) is not detectable. What is fixed before the model call
is *which* batches were searched and with which config. L0: our crawler key vouches for what the page said.

Run: `$NEWS_DATA/venv/bin/pip install -r news/requirements.lock`; `python -m newsreg.crawl`, `python -m newsreg.register`
(from `news/`); `news/jobs/newsjobs.sh start|stop|status` runs both on a schedule; `pytest news/tests`; `forge test`.
