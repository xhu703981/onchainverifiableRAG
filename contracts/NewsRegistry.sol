// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title NewsRegistry
/// @notice Daily news batches as Merkle roots (scheme "news-merkle-v1", see news/NORMALIZATION.md).
/// Only the batch root, the manifest hash and the manifest URI go on-chain; article text stays off-chain.
/// Registration is permissionless; trust is the owner's allowlist of publishers (same pattern as
/// SourceRegistry). Retrieval must filter on `isTrustedRoot`, not on `isRegistered`.
/// @dev `isTrustedRoot` reads the CURRENT allowlist: removing a publisher un-trusts every batch it
/// registered in the past. That is intended (a leaked crawler key can be revoked retroactively).
contract NewsRegistry {
    struct Batch {
        bytes32 manifestHash; // sha256(manifest.json bytes)
        address registrant;
        uint32 articleCount;
        uint64 timestamp; // block time of registration: a proven "existed no later than" bound
    }

    mapping(bytes32 => Batch) private batches;
    mapping(bytes32 => string) public manifestURI;
    bytes32[] public roots;

    address public owner;
    mapping(address => bool) public trustedPublishers;

    event BatchRegistered(
        bytes32 indexed root, bytes32 manifestHash, address indexed registrant, uint32 articleCount, uint256 timestamp
    );
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    event TrustedPublisherAdded(address indexed publisher);
    event TrustedPublisherRemoved(address indexed publisher);

    error RootAlreadyRegistered(bytes32 root);
    error ZeroValue();
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

    /// @notice Register a batch root. A root can be registered once. Exception: if it is held by an
    /// address that is not trusted, a trusted publisher may take it over, so nobody can squat a root
    /// seen in the mempool or in a published manifest and make it permanently untrusted.
    function registerBatch(bytes32 root, bytes32 manifestHash, uint32 articleCount, string calldata uri) external {
        if (root == bytes32(0) || manifestHash == bytes32(0) || articleCount == 0) revert ZeroValue();
        Batch storage b = batches[root];
        if (b.timestamp != 0) {
            if (trustedPublishers[b.registrant] || !trustedPublishers[msg.sender]) revert RootAlreadyRegistered(root);
        } else {
            roots.push(root);
        }
        batches[root] = Batch(manifestHash, msg.sender, articleCount, uint64(block.timestamp));
        manifestURI[root] = uri;
        emit BatchRegistered(root, manifestHash, msg.sender, articleCount, block.timestamp);
    }

    function isRegistered(bytes32 root) external view returns (bool) {
        return batches[root].timestamp != 0;
    }

    /// @notice registered AND its registrant is on the current trusted-publisher allowlist
    function isTrustedRoot(bytes32 root) public view returns (bool) {
        Batch storage b = batches[root];
        return b.timestamp != 0 && trustedPublishers[b.registrant];
    }

    function areTrustedRoots(bytes32[] calldata rs) external view returns (bool[] memory out) {
        out = new bool[](rs.length);
        for (uint256 i; i < rs.length; ++i) out[i] = isTrustedRoot(rs[i]);
    }

    function getBatch(bytes32 root)
        external
        view
        returns (bool exists, bytes32 manifestHash, address registrant, uint32 articleCount, uint64 timestamp, string memory uri)
    {
        Batch storage b = batches[root];
        return (b.timestamp != 0, b.manifestHash, b.registrant, b.articleCount, b.timestamp, manifestURI[root]);
    }

    function batchCount() external view returns (uint256) {
        return roots.length;
    }
}
