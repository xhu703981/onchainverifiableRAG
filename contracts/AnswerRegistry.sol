// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title AnswerRegistry
/// @notice One 32-byte commitment per RAG answer. The root is a Merkle root (SHA-256, domain separated)
/// over a fixed, ordered list of labeled leaves: corpus root, retrieval config, an anchor Arc block,
/// question, ranked candidates, request (prompt), requested/served model, provider, response, answer and
/// self-reported step times. Anyone holding the answer bundle recomputes the root and compares it with
/// recordOf(root). The record's block is the "not after" bound; the anchor block inside the root is the
/// "not before" bound.
///
///   leaf  = sha256(0x00 || sha256(label || 0x00 || value))
///   node  = sha256(0x01 || left || right)        odd node at the end of a level is promoted unchanged
contract AnswerRegistry {
    struct Record {
        address recorder;
        uint64 blockNumber;
        uint64 timestamp;
    }

    address public owner;
    mapping(address => bool) public isRecorder;
    mapping(bytes32 => Record) private records;

    event AnswerRecorded(bytes32 indexed root, address indexed recorder, uint256 blockNumber, uint256 timestamp);
    event RecorderSet(address indexed recorder, bool allowed);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    error NotOwner();
    error NotRecorder();
    error ZeroValue();
    error AlreadyRecorded(bytes32 root);

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    constructor() {
        owner = msg.sender;
        isRecorder[msg.sender] = true;
        emit OwnershipTransferred(address(0), msg.sender);
        emit RecorderSet(msg.sender, true);
    }

    /// @notice Record one answer root. Only allow-listed recorders (so nobody can front-run and squat a root).
    function record(bytes32 root) external {
        if (!isRecorder[msg.sender]) revert NotRecorder();
        if (root == bytes32(0)) revert ZeroValue();
        if (records[root].recorder != address(0)) revert AlreadyRecorded(root);
        records[root] = Record(msg.sender, uint64(block.number), uint64(block.timestamp));
        emit AnswerRecorded(root, msg.sender, block.number, block.timestamp);
    }

    /// @notice (recorder, blockNumber, timestamp); recorder is address(0) if the root was never recorded.
    function recordOf(bytes32 root) external view returns (address recorder, uint64 blockNumber, uint64 timestamp) {
        Record storage r = records[root];
        return (r.recorder, r.blockNumber, r.timestamp);
    }

    function setRecorder(address who, bool allowed) external onlyOwner {
        if (who == address(0)) revert ZeroValue();
        isRecorder[who] = allowed;
        emit RecorderSet(who, allowed);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert ZeroValue();
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }
}
