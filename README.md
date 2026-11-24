# News batch manifests (Arc testnet NewsRegistry)

Each file here is the exact manifest whose sha256 is the `manifestHash` registered on-chain for its batch root
in NewsRegistry on Arc testnet (chain 5042002). Manifests hold article metadata and Merkle roots only,
never article text. Files are written by the registration job; never edit them (the hash would no longer match).
Scheme: news-merkle-v1, see `news/NORMALIZATION.md` on the main branch.
