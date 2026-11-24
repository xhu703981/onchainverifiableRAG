# Normalization spec (news-merkle-v1)

The sha256 of this file's exact bytes is the `normalization` field of every article's meta.
Changing a single byte here produces a new spec version. Git keeps this file byte-exact (`-text` in .gitattributes).

## Input
The raw HTTP response body bytes saved by the fetcher (`raw/<raw_html_sha256>.html`), unmodified.
`raw_html_sha256` = sha256 of those bytes, and it is part of the article meta.

## Extraction
1. `trafilatura.extract(raw_bytes, output_format="txt", favor_precision=True, include_comments=False,
   include_tables=False, include_images=False, include_links=False, include_formatting=False,
   deduplicate=False, with_metadata=False)`, with the exact versions in `news/requirements.lock`
   (trafilatura, lxml, charset-normalizer, justext, htmldate, courlan, ...). trafilatura decodes the
   bytes itself (charset detection is part of the pinned environment).
2. `extractor_version` = `trafilatura-<version>/lock-<first 16 hex of sha256(requirements.lock)>`.

## Paragraphs
1. Split the extracted text on `\n`. Each line is one paragraph, in document order.
2. For each paragraph, in this order:
   - Unicode NFC (`unicodedata.normalize("NFC", p)`).
   - Collapse whitespace: every run of Unicode whitespace (Python `str.split()` with no argument)
     becomes one ASCII space. This also strips leading and trailing whitespace.
3. Drop empty paragraphs.
4. An article with 0 paragraphs is never registered. Articles with fewer than 3 paragraphs or fewer
   than 400 characters in total are also kept out of batches (video/live pages, teasers).

## Meta (bound into the article leaf)
`{url, outlet, title, published_at, fetched_at, extractor_version, normalization, raw_html_sha256, version}`
- `url`: the feed link after URL normalization: lowercase scheme and host, drop the default port and
  the fragment, drop tracking query parameters (`utm_*`, `at_*`, `mc_*`, `pk_*`, `hsa_*`, `itm_*`, `ga_*`,
  `fbclid`, `gclid`, `cmpid`, `ocid`, `ref`, `src`, `rss`, ... full list in `newsreg/scheme.py`).
  The remaining parameters keep their order. Articles are deduplicated across feeds by this URL and
  by `article_root`.
- `title`: the feed title, with the same NFC + whitespace rules as paragraphs.
- `published_at`: claimed by the feed. `fetched_at`: claimed by our crawler (trust level L0). Both are
  ISO 8601 UTC, `YYYY-MM-DDTHH:MM:SSZ`, or `null` if the feed gives no date.
- `version`: 1 (meta schema version; edit history will increment it later).
- Canonical form: RFC 8785 JSON Canonicalization Scheme (JCS), UTF-8.

## Hashing
- paragraph leaf = sha256(0x00 || utf8(paragraph))
- node = sha256(0x01 || left || right); an odd last node is promoted unchanged (no duplication)
- one leaf: root = leaf
- article_root = Merkle(paragraph leaves); article leaf = sha256(0x02 || sha256(JCS(meta)) || article_root)
- batch_root = Merkle(article leaves, manifest order)
- manifest = JCS of `{scheme, batch_root, article_count, created_at, articles: [{meta, article_root}]}`;
  manifest_hash = sha256(file bytes). Manifests hold metadata and roots only, never article text.
