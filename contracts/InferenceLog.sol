// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title InferenceLog
/// @notice Write-once, two-phase on-chain log of LLM calls made by a RAG service.
/// Phase 1 `commit` (before the model call): the SHA-256 of the exact HTTP request body
/// (model, params, full prompt incl. passages and a client timestamp) and a root over the
/// ordered passage hashes. Phase 2 `finalize` (after the call, in a LATER block): the SHA-256
/// of the exact raw response body. Only hashes are stored; texts stay off-chain in the bundle.
/// This proves that a request with exactly these bytes was committed before the answer was
/// recorded, and binds answer <-> prompt <-> passages. It does NOT prove the provider ran it
/// (see README "Verify the model call" for the trust gap).
contract InferenceLog {
    struct Record {
        bytes32 questionId;   // sha256("<dataset>:<qid>")
        bytes32 requestHash;  // sha256(exact request body bytes)
        bytes32 passagesRoot; // sha256(concat(passageHash_1 .. passageHash_n)), prompt order
        bytes32 modelHash;    // sha256(model id string, e.g. "openai/gpt-4o-mini")
        bytes32 responseHash; // sha256(exact raw response body bytes); 0 until finalized
        address logger;       // who committed
        uint16 passageCount;
        uint64 commitBlock;
        uint64 commitTime;
        uint64 finalizeBlock;
        uint64 finalizeTime;
    }

    address public owner;
    mapping(address => bool) public isLogger;
    mapping(bytes32 => Record) private records;
    uint256 public totalCommitted;

    event Committed(bytes32 indexed answerId, bytes32 indexed questionId, bytes32 requestHash, bytes32 passagesRoot,
        bytes32 modelHash, uint16 passageCount, address indexed logger, uint256 timestamp);
    event Finalized(bytes32 indexed answerId, bytes32 responseHash, address indexed logger, uint256 timestamp);
    event LoggerSet(address indexed logger, bool allowed);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    error NotOwner();
    error NotLogger();
    error ZeroValue();
    error AlreadyCommitted(bytes32 answerId);
    error NotCommitted(bytes32 answerId);
    error AlreadyFinalized(bytes32 answerId);
    error SameBlockAsCommit(bytes32 answerId);

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    modifier onlyLogger() {
        if (!isLogger[msg.sender]) revert NotLogger();
        _;
    }

    constructor() {
        owner = msg.sender;
        isLogger[msg.sender] = true;
        emit OwnershipTransferred(address(0), msg.sender);
        emit LoggerSet(msg.sender, true);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert ZeroValue();
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }

    function setLogger(address logger, bool allowed) external onlyOwner {
        if (logger == address(0)) revert ZeroValue();
        isLogger[logger] = allowed;
        emit LoggerSet(logger, allowed);
    }

    /// @notice Phase 1, sent BEFORE calling the model. Write-once per answerId.
    function commit(bytes32 answerId, bytes32 questionId, bytes32 requestHash, bytes32 passagesRoot, bytes32 modelHash,
        uint16 passageCount) external onlyLogger {
        if (answerId == bytes32(0) || requestHash == bytes32(0)) revert ZeroValue();
        if (records[answerId].commitBlock != 0) revert AlreadyCommitted(answerId);
        records[answerId] = Record({
            questionId: questionId,
            requestHash: requestHash,
            passagesRoot: passagesRoot,
            modelHash: modelHash,
            responseHash: bytes32(0),
            logger: msg.sender,
            passageCount: passageCount,
            commitBlock: uint64(block.number),
            commitTime: uint64(block.timestamp),
            finalizeBlock: 0,
            finalizeTime: 0
        });
        unchecked { ++totalCommitted; }
        emit Committed(answerId, questionId, requestHash, passagesRoot, modelHash, passageCount, msg.sender, block.timestamp);
    }

    /// @notice Phase 2, sent AFTER the model answered. Once only, strictly in a later block than the commit.
    function finalize(bytes32 answerId, bytes32 responseHash) external onlyLogger {
        Record storage r = records[answerId];
        if (r.commitBlock == 0) revert NotCommitted(answerId);
        if (r.finalizeBlock != 0) revert AlreadyFinalized(answerId);
        if (responseHash == bytes32(0)) revert ZeroValue();
        if (block.number <= r.commitBlock) revert SameBlockAsCommit(answerId);
        r.responseHash = responseHash;
        r.finalizeBlock = uint64(block.number);
        r.finalizeTime = uint64(block.timestamp);
        emit Finalized(answerId, responseHash, msg.sender, block.timestamp);
    }

    function getRecord(bytes32 answerId) external view returns (Record memory) {
        return records[answerId];
    }

    function isFinalized(bytes32 answerId) external view returns (bool) {
        return records[answerId].finalizeBlock != 0;
    }
}
