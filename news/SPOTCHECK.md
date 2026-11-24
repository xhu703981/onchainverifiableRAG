# Extraction spot-check (2026-11-24, first crawl)

30 random articles from batch 1 (`python -m newsreg.spotcheck 30`, seed 2026), read by hand: every paragraph
flagged by a boilerplate pattern or shorter than 60 characters was inspected, plus the first and last 3 paragraphs.

**Result: 23/30 fully clean, 7/30 with a one-line residue, 0 with nav menus, ads or "related" blocks.**
Section sub-headings ("A way forward", "Weeks of violence") are kept on purpose: they are article text.

| outlet | sampled | residue | what |
|---|---|---|---|
| The Straits Times | 4 | 3 | a one-line UI label `AI generated` (above an AI summary box) as paragraph 1 |
| NPR | 4 | 3 | trailing credit/footer lines: "Broadcast and digital story edited by…", the KFF Health News boilerplate, a newsletter plug |
| BBC News | 1 | 1 | a `- Published` line as paragraph 0 |
| Phys.org | 2 | 0* | *ends with "Publication details / More information" + the paper's DOI; kept, it cites the study |
| others (Conversation ×2, RTE ×2, Euronews ×2, Ars Technica ×3, TechCrunch ×4, Fox ×3, France 24, CNBC, Guardian) | 19 | 0 | clean |

Pipeline-level failures per outlet (whole crawl, not just the sample): France 24 7/12 `short` (video pages,
kept out of batches), NASA 2/10 `short` (APOD image pages), Al Jazeera 1/12 `short` (video), BBC 1/12 `empty`
(video), RTE 1/12 `empty`, Global Voices 2/12 transient TLS errors (now retried on the next crawl).

The residue lines are harmless for integrity (they are hashed like any paragraph) but could be cited. Fixing them
means a new extraction rule, which is a new NORMALIZATION.md version (its sha256 is in every article's meta), so it
is left for a deliberate spec bump rather than changed under already-registered batches.
