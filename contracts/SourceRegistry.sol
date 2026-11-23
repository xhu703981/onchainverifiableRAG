// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title SourceRegistry
/// @notice Content-hash (SHA-256) registry for RAG sources.
/// Registration stays permissionless; trust is expressed separately through an
/// owner-managed allowlist of publishers. Retrieval should filter on
/// `isVerified` (registered AND registrant is trusted), not on `isRegistered`.
contract SourceRegistry {
    struct SourceRecord {
        bytes32 contentHash;
        string cid;
        string sourceUri;
        address registrant;
        uint256 timestamp;
    }

    /// @dev Compact one-slot record used by the batch path.
    struct BatchRecord {
        address registrant;
        uint64 timestamp;
    }

    mapping(bytes32 => SourceRecord) public sources;
    bytes32[] public allHashes;

    mapping(bytes32 => BatchRecord) public batchSources;
    uint256 public batchSourceCount;

    address public owner;
    mapping(address => bool) public trustedPublishers;

    event SourceRegistered(bytes32 indexed contentHash, string cid, address indexed registrant, uint256 timestamp);
    event SourceUpdated(bytes32 indexed contentHash, string cid, address indexed registrant);
    event BatchRegistered(address indexed registrant, uint256 submitted, uint256 added, uint256 timestamp);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    event TrustedPublisherAdded(address indexed publisher);
    event TrustedPublisherRemoved(address indexed publisher);

    error SourceAlreadyRegistered(bytes32 contentHash);
    error SourceNotFound(bytes32 contentHash);
    error EmptyCID();
    error NotOwner();
    error ZeroAddress();

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    constructor() {
        owner = msg.sender;
        emit OwnershipTransferred(address(0), msg.sender);
    }

    // ---------------------------------------------------------------- owner

    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert ZeroAddress();
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }

    function addTrustedPublisher(address publisher) external onlyOwner {
        if (publisher == address(0)) revert ZeroAddress();
        trustedPublishers[publisher] = true;
        emit TrustedPublisherAdded(publisher);
    }

    function removeTrustedPublisher(address publisher) external onlyOwner {
        trustedPublishers[publisher] = false;
        emit TrustedPublisherRemoved(publisher);
    }

    function isTrustedPublisher(address publisher) external view returns (bool) {
        return trustedPublishers[publisher];
    }

    // --------------------------------------------------------- registration

    function registerSource(bytes32 contentHash, string calldata cid, string calldata sourceUri) external {
        if (bytes(cid).length == 0) revert EmptyCID();
        if (sources[contentHash].timestamp != 0 || batchSources[contentHash].timestamp != 0) {
            revert SourceAlreadyRegistered(contentHash);
        }

        sources[contentHash] = SourceRecord({
            contentHash: contentHash,
            cid: cid,
            sourceUri: sourceUri,
            registrant: msg.sender,
            timestamp: block.timestamp
        });
        allHashes.push(contentHash);
        emit SourceRegistered(contentHash, cid, msg.sender, block.timestamp);
    }

    /// @notice Gas-cheap bulk registration: one storage slot per hash.
    /// The CID is not stored; for small files hashed as a single raw IPFS block
    /// it is derived from the hash itself (see `cidOf`). Hashes that are already
    /// registered (by anyone, via either path) are skipped, so retries are idempotent.
    /// @return added number of newly registered hashes
    function registerBatch(bytes32[] calldata contentHashes) external returns (uint256 added) {
        uint64 ts = uint64(block.timestamp);
        for (uint256 i = 0; i < contentHashes.length; ++i) {
            bytes32 h = contentHashes[i];
            if (batchSources[h].timestamp != 0 || sources[h].timestamp != 0) continue;
            batchSources[h] = BatchRecord({registrant: msg.sender, timestamp: ts});
            unchecked { ++added; }
        }
        batchSourceCount += added;
        emit BatchRegistered(msg.sender, contentHashes.length, added, block.timestamp);
    }

    function updateSource(bytes32 contentHash, string calldata cid, string calldata sourceUri) external {
        SourceRecord storage rec = sources[contentHash];
        if (rec.timestamp == 0) revert SourceNotFound(contentHash);
        if (rec.registrant != msg.sender) revert("Only registrant can update");
        if (bytes(cid).length == 0) revert EmptyCID();
        rec.cid = cid;
        rec.sourceUri = sourceUri;
        emit SourceUpdated(contentHash, cid, msg.sender);
    }

    // ---------------------------------------------------------------- views

    function getSource(bytes32 contentHash) external view returns (bool exists, string memory cid, string memory sourceUri, address registrant, uint256 timestamp) {
        SourceRecord memory rec = sources[contentHash];
        if (rec.timestamp != 0) return (true, rec.cid, rec.sourceUri, rec.registrant, rec.timestamp);
        BatchRecord memory b = batchSources[contentHash];
        if (b.timestamp != 0) return (true, cidOf(contentHash), "", b.registrant, b.timestamp);
        return (false, "", "", address(0), 0);
    }

    function isRegistered(bytes32 contentHash) public view returns (bool) {
        return sources[contentHash].timestamp != 0 || batchSources[contentHash].timestamp != 0;
    }

    function registrantOf(bytes32 contentHash) public view returns (address) {
        if (sources[contentHash].timestamp != 0) return sources[contentHash].registrant;
        return batchSources[contentHash].registrant; // address(0) if unknown
    }

    /// @notice Registered AND registered by a currently trusted publisher.
    function isVerified(bytes32 contentHash) public view returns (bool) {
        address r = registrantOf(contentHash);
        return r != address(0) && trustedPublishers[r];
    }

    function areVerified(bytes32[] calldata contentHashes) external view returns (bool[] memory out) {
        out = new bool[](contentHashes.length);
        for (uint256 i = 0; i < contentHashes.length; ++i) out[i] = isVerified(contentHashes[i]);
    }

    function areRegistered(bytes32[] calldata contentHashes) external view returns (bool[] memory out) {
        out = new bool[](contentHashes.length);
        for (uint256 i = 0; i < contentHashes.length; ++i) out[i] = isRegistered(contentHashes[i]);
    }

    /// @notice Sources registered via `registerSource` (unchanged semantics).
    function totalSources() external view returns (uint256) {
        return allHashes.length;
    }

    /// @notice Sources registered via either path.
    function totalAllSources() external view returns (uint256) {
        return allHashes.length + batchSourceCount;
    }

    /// @notice CIDv1 (raw codec 0x55, sha2-256 multihash) for a SHA-256 digest, base32-lower
    /// multibase ("b..."). Equals `ipfs add --cid-version 1 --raw-leaves` for files that fit
    /// in one block (<= 256 KiB with default chunker).
    function cidOf(bytes32 digest) public pure returns (string memory) {
        bytes memory data = abi.encodePacked(bytes4(0x01551220), digest); // 36 bytes
        bytes memory alphabet = "abcdefghijklmnopqrstuvwxyz234567";
        bytes memory out = new bytes(1 + (data.length * 8 + 4) / 5); // 1 + 58
        out[0] = "b";
        uint256 buffer;
        uint256 bits;
        uint256 j = 1;
        for (uint256 i = 0; i < data.length; ++i) {
            buffer = (buffer << 8) | uint8(data[i]);
            bits += 8;
            while (bits >= 5) {
                bits -= 5;
                out[j++] = alphabet[(buffer >> bits) & 31];
            }
        }
        if (bits > 0) out[j++] = alphabet[(buffer << (5 - bits)) & 31];
        return string(out);
    }
}
