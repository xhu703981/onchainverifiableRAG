# Data shapes, news-merkle-v1

Each piece of state has one owner. The local DB owns article text. The chain owns trust and registration time. The manifest owns the list of article metas in a batch.

| shape | fields | owner | defined in |
|---|---|---|---|
| meta | url, outlet, title, published_at, fetched_at, extractor_version, normalization, raw_html_sha256, version | DB `articles.meta_json`, copied into the manifest | `newsreg/scheme.py` `META_KEYS` |
| proof | list of `"L:0x.."` or `"R:0x.."`, bottom-up, the sibling's side; a level where the node moved up adds no step | DB (`paragraphs.proof`, `articles.batch_proof`) | `scheme.proof`, `scheme.apply_proof` |
| manifest | JCS of {scheme, batch_root, article_count, created_at, articles: [{meta, article_root}]} | file on the `news-manifests` branch; its sha256 is on-chain | `register.build_manifest` |
| batch | root, manifestHash, registrant, articleCount, timestamp, manifestURI | NewsRegistry on Arc | `contracts/NewsRegistry.sol` |
| citation in a bundle | id, text, content_hash, paragraph {leaf, proof}, article {meta, article_root, leaf, proof}, batch {root, manifest_hash, manifest_uri, article_count, tx, block, block_time, explorer} | built per answer | `engine.NewsEngine.evidence` |
| news bundle | kind "news", question, answer, as_of, as_of_statement, news {registry, scheme, scope {roots, scope_hash}, config_hash, config_json}, citations, inference (same as the main demo) or abstain {reason, detail} | built per answer | `engine.NewsEngine.ask` |

DB tables: `articles` (one row per normalized URL, with status ok, short, empty, duplicate or error), `paragraphs` (text, leaf, proof to the article root), `batches` (status built or registered, tx, block, block_time), `feed_runs`.

Rules that cross boundaries:
- Hashes are `0x` plus 64 lowercase hex characters everywhere except checksummed addresses.
- Times are ISO 8601 UTC with `Z` and second precision. Block times are unix seconds.
- The bundle carries no field that the verifier does not check. Position indexes were removed for that reason.
