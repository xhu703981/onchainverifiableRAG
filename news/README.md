# Verifiable news answers

This folder answers news questions from registered news paragraphs only. You can check where each cited paragraph came from without trusting our server. You can't use it to check whether the news is true.

## What a passing bundle proves

A news answer comes with a bundle. When `verify_citations.py` or the news page passes the bundle, two things are proven.

1. Every cited paragraph comes from an article in a registered batch that a trusted key put on Arc testnet. The paragraph text, the article URL, the outlet, the title, and the claimed times are all inside the batch's Merkle root. A change to any of them breaks the proof.
2. The cited paragraphs were in the prompt that the server committed on-chain before the model call. The `InferenceLog` commit transaction holds the hash of the exact request. The finalize transaction holds the hash of the exact response, in a later block. The request also names the batches that retrieval searched and the hash of the retrieval config.

## What it does not prove

- It does not prove that the news is true.
- It does not prove that no relevant article was left out. Copyright stops us from publishing full article text, so you can't rerun retrieval yourself. An article that contradicts the answer could be missing, and the checks would still pass.
- It does not remove trust in our crawler. Our crawler key vouches for what each page said when we fetched it. We call this trust level L0. The `published_at` time comes from the feed and the `fetched_at` time comes from our crawler. Both are claims. The batch block time is the only proven time.

## How articles get registered

1. `sources.json` lists 20 English RSS feeds.
2. Every hour, `newsreg.crawl` fetches new articles and saves the raw HTML.
3. trafilatura splits each article into paragraphs. `NORMALIZATION.md` holds the rules, and `requirements.lock` pins the libraries.
4. Every 8 hours, `newsreg.register` builds one batch.
5. The job publishes the batch manifest on the [`news-manifests` branch](https://github.com/xhu703981/onchainverifiableRAG/tree/news-manifests/manifests).
6. The job registers the batch root and the manifest hash in `NewsRegistry` on Arc testnet.

Article text stays in the local database. Manifests hold metadata and hashes, never article text.

The fetch step sits behind the `Fetcher` interface. A fetcher that proves the TLS session, called zkTLS, can replace `HttpFetcher` later without other changes.

## Hashes

The scheme is called `news-merkle-v1`.

```
paragraph leaf = sha256(0x00 || paragraph text)
node           = sha256(0x01 || left || right)
article leaf   = sha256(0x02 || sha256(JCS(meta)) || article root)
batch root     = Merkle root over the article leaves
```

- An odd last node moves up a level unchanged. A tree with one leaf has that leaf as its root.
- The article root is the Merkle root over the article's paragraph leaves.
- JCS is the RFC 8785 canonical JSON form.
- The meta fields are `url`, `outlet`, `title`, `published_at`, `fetched_at`, `extractor_version`, `normalization`, `raw_html_sha256`, and `version`.

[`docs/news/SHAPES.md`](../docs/news/SHAPES.md) lists every data shape.

## How answers are made

Retrieval searches the paragraphs of batches that `isTrustedRoot` accepts at question time.

The server answers `Not found in registered news.` without a model call in two cases:

- No paragraph passes the relevance threshold in `retrieval_news_v1.json`.
- The question names a date after the cutoff.

In all other cases, the prompt lists the paragraphs, and every sentence of the answer must cite one. The news page marks a sentence without a citation as unverified.

The cutoff, `as_of`, is the block time of the latest batch that retrieval searched.

## Trust rules

`isTrustedRoot` reads the current allowlist. If the owner removes a key, every batch that the key registered stops being trusted.

Anyone can register a root. If an untrusted address registers a root first, a trusted key can take the root over. This rule stops anyone from blocking a root that they saw in a published manifest.

## Run and verify

[`RUNNING.md`](RUNNING.md) has the commands to crawl, register, test, and verify a bundle.
