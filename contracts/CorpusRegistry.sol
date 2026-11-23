// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title CorpusRegistry
/// @notice Versioned, append-only commitments to a whole retrieval corpus.
/// A publisher posts the Merkle root over every passage the retriever searches, the number of
/// leaves, and the hash of the exact retrieval configuration (retriever code, embedding model
/// weights, tokenizer, parameters). Answers then name a version; anyone can (a) check a passage
/// is in that corpus with an inclusion proof and (b) replay retrieval over it to confirm the
/// ranked candidates the answer used are exactly what the committed retriever returns.
///
/// Merkle tree (SHA-256, domain separated, documented in webdemo/README.md):
///   leaf   = sha256(0x00 || passageHash)      passageHash = sha256(utf-8 passage text)
///   node   = sha256(0x01 || left || right)
///   leaves sorted ascending by passageHash; an odd node at the end of a level is promoted unchanged.
contract CorpusRegistry {
    struct Corpus {
        bytes32 root;        // Merkle root over all passages
        bytes32 configHash;  // sha256(retrieval config JSON bytes)
        uint32 leafCount;    // number of passages
        address publisher;
        uint64 blockNumber;
        uint64 timestamp;
        string uri;          // where the corpus + config can be fetched (content is hash-checked)
    }

    address public owner;
    mapping(address => bool) public isPublisher;
    Corpus[] private versions; // version v is versions[v - 1]

    event CorpusPublished(uint256 indexed version, bytes32 indexed root, bytes32 configHash, uint32 leafCount,
        string uri, address indexed publisher);
    event PublisherSet(address indexed publisher, bool allowed);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    error NotOwner();
    error NotPublisher();
    error ZeroValue();
    error NoSuchVersion(uint256 version);

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    constructor() {
        owner = msg.sender;
        isPublisher[msg.sender] = true;
        emit OwnershipTransferred(address(0), msg.sender);
        emit PublisherSet(msg.sender, true);
    }

    function publish(bytes32 root, uint32 leafCount, bytes32 configHash, string calldata uri) external returns (uint256 version) {
        if (!isPublisher[msg.sender]) revert NotPublisher();
        if (root == bytes32(0) || configHash == bytes32(0) || leafCount == 0) revert ZeroValue();
        versions.push(Corpus(root, configHash, leafCount, msg.sender, uint64(block.number), uint64(block.timestamp), uri));
        version = versions.length;
        emit CorpusPublished(version, root, configHash, leafCount, uri, msg.sender);
    }

    /// @notice Static fields of a version (easy to decode: seven 32-byte words).
    function corpusInfo(uint256 version) external view returns (bytes32 root, bytes32 configHash, uint32 leafCount,
        address publisher, uint64 blockNumber, uint64 timestamp) {
        Corpus storage c = _get(version);
        return (c.root, c.configHash, c.leafCount, c.publisher, c.blockNumber, c.timestamp);
    }

    function corpusUri(uint256 version) external view returns (string memory) {
        return _get(version).uri;
    }

    function latestVersion() external view returns (uint256) {
        return versions.length;
    }

    function setPublisher(address who, bool allowed) external onlyOwner {
        if (who == address(0)) revert ZeroValue();
        isPublisher[who] = allowed;
        emit PublisherSet(who, allowed);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert ZeroValue();
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }

    function _get(uint256 version) private view returns (Corpus storage) {
        if (version == 0 || version > versions.length) revert NoSuchVersion(version);
        return versions[version - 1];
    }
}
