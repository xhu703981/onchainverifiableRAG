# Verifiable news registry, phase 1

This checks that an AI answer's information comes, word for word, from news sources you choose to trust.
It does not check that the news is true.

## Pipeline

1. `sources.json` lists 20 English RSS feeds.
2. Every hour, `newsreg.crawl` reads the feeds and fetches new articles through the `Fetcher` interface. `HttpFetcher` is trust level L0, which means our crawler vouches for what the page said. A zkTLS fetcher can replace it later.
3. The raw HTML is saved. trafilatura extracts paragraphs under the rules in `NORMALIZATION.md`, with the whole environment pinned in `requirements.lock`.
4. Paragraphs and articles go into a Merkle tree, scheme `news-merkle-v1`.
5. Every 8 hours, `newsreg.register` builds one batch, publishes its manifest on the [`news-manifests`](https://github.com/xhu703981/onchainverifiableRAG/tree/news-manifests/manifests) branch and registers the root and manifest hash in NewsRegistry on Arc testnet.

Article text stays in the local database. It is never on-chain and never in a manifest.

```
paragraph leaf = sha256(0x00 || text)
node           = sha256(0x01 || left || right); an odd last node moves up unchanged; one leaf: root = leaf
article leaf   = sha256(0x02 || sha256(JCS(meta)) || article_root)
meta           = {url, outlet, title, published_at, fetched_at, extractor_version, normalization, raw_html_sha256, version}
batch root     = Merkle(article leaves), registered with registerBatch(root, manifestHash, articleCount, manifestURI)
```

## Answering

Retrieval uses the committed BM25 and dense retriever with `retrieval_news_v1.json`. It searches only paragraphs in batches that `isTrustedRoot` accepts at question time.

The answer is `Not found in registered news.`, with no model call, in two cases: no paragraph passes the relevance threshold, or the question names a date after the cutoff.

Otherwise the prompt holds the paragraphs as [P1] to [Pn]. The request is committed to InferenceLog before the model call, with the same contract and two transactions as the main demo. Its system line holds the scope hash over the sorted batch roots, the config hash and `as_of`.

Every sentence must cite a paragraph. The page marks a sentence without a citation as unverified.

`as_of` is the block time of the latest batch in scope, which proves the articles existed no later than that time. `published_at` comes from the feed and `fetched_at` from our crawler. Both are claims.

## Verify

Run `python webdemo/verify_citations.py bundle.json`. It hands news bundles to `verify_news.py`. Add `--trust 0x...` to use your own trusted keys, or `--outlets "BBC News" ...` to accept only the outlets you chose.

The page served by `news/app.py` runs the same checks in your browser with `newsverify.js`, calling the Arc RPC directly.

The checks go from the text to the paragraph proof, the article root, the article leaf with its meta, the batch root, `isTrustedRoot` on-chain, the registration transaction and the published manifest. The model call checks M1 to M6 are unchanged.

## Trust rules

`isTrustedRoot` reads the current allowlist, so revoking a key un-trusts every batch that key registered.

Anyone can register a root. If an untrusted address registered a root first, a trusted publisher can take it over, so nobody can block a root they saw in a manifest or in the mempool.

## What this does not prove

It does not prove the news is true.

It cannot show that an article was left out. Full text is not redistributed because of copyright, so nobody can replay retrieval. What the commit fixes before the model call is which batches were searched, and with which config.

At L0 you trust our crawler key for what each page said at fetch time.

## Run

From `news/`, with `$NEWS_DATA/venv/bin/pip install -r requirements.lock` done first:

- `python -m newsreg.crawl` and `python -m newsreg.register` run one cycle each.
- `jobs/newsjobs.sh start|stop|status` runs both on a schedule.
- `pytest tests` and, at the repo root, `forge test`.

Data shapes and the decision log are in [`docs/news/`](../docs/news/).
