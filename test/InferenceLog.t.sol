// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Test} from "forge-std/Test.sol";
import {InferenceLog} from "../contracts/InferenceLog.sol";

contract InferenceLogTest is Test {
    InferenceLog ilog;
    address other = makeAddr("other");
    address logger2 = makeAddr("logger2");
    bytes32 constant A = keccak256("answer-1");
    bytes32 constant Q = keccak256("nq:test16");
    bytes32 constant REQ = keccak256("request");
    bytes32 constant ROOT = keccak256("root");
    bytes32 constant MODEL = keccak256("openai/gpt-4o-mini");
    bytes32 constant RESP = keccak256("response");

    event Committed(bytes32 indexed answerId, bytes32 indexed questionId, bytes32 requestHash, bytes32 passagesRoot,
        bytes32 modelHash, uint16 passageCount, address indexed logger, uint256 timestamp);
    event Finalized(bytes32 indexed answerId, bytes32 responseHash, address indexed logger, uint256 timestamp);

    function setUp() public {
        ilog = new InferenceLog();
    }

    function _commit() internal {
        ilog.commit(A, Q, REQ, ROOT, MODEL, 5);
    }

    function test_deployerIsOwnerAndLogger() public view {
        assertEq(ilog.owner(), address(this));
        assertTrue(ilog.isLogger(address(this)));
    }

    function test_commitThenFinalize_recordAndEvents() public {
        vm.roll(100); vm.warp(1_800_000_000);
        vm.expectEmit(true, true, true, true);
        emit Committed(A, Q, REQ, ROOT, MODEL, 5, address(this), 1_800_000_000);
        _commit();
        assertFalse(ilog.isFinalized(A));
        vm.roll(101); vm.warp(1_800_000_002);
        vm.expectEmit(true, true, false, true);
        emit Finalized(A, RESP, address(this), 1_800_000_002);
        ilog.finalize(A, RESP);
        InferenceLog.Record memory r = ilog.getRecord(A);
        assertEq(r.questionId, Q); assertEq(r.requestHash, REQ); assertEq(r.passagesRoot, ROOT);
        assertEq(r.modelHash, MODEL); assertEq(r.responseHash, RESP); assertEq(r.logger, address(this));
        assertEq(r.passageCount, 5); assertEq(r.commitBlock, 100); assertEq(r.commitTime, 1_800_000_000);
        assertEq(r.finalizeBlock, 101); assertEq(r.finalizeTime, 1_800_000_002);
        assertTrue(ilog.isFinalized(A)); assertEq(ilog.totalCommitted(), 1);
    }

    function test_writeOnce() public {
        _commit();
        vm.expectRevert(abi.encodeWithSelector(InferenceLog.AlreadyCommitted.selector, A));
        ilog.commit(A, Q, keccak256("other request"), ROOT, MODEL, 5);
        vm.roll(block.number + 1);
        ilog.finalize(A, RESP);
        vm.roll(block.number + 1);
        vm.expectRevert(abi.encodeWithSelector(InferenceLog.AlreadyFinalized.selector, A));
        ilog.finalize(A, keccak256("other response"));
    }

    function test_finalizeRequiresCommitAndLaterBlock() public {
        vm.expectRevert(abi.encodeWithSelector(InferenceLog.NotCommitted.selector, A));
        ilog.finalize(A, RESP);
        _commit();
        vm.expectRevert(abi.encodeWithSelector(InferenceLog.SameBlockAsCommit.selector, A));
        ilog.finalize(A, RESP);
    }

    function test_zeroValuesRejected() public {
        vm.expectRevert(InferenceLog.ZeroValue.selector);
        ilog.commit(bytes32(0), Q, REQ, ROOT, MODEL, 1);
        vm.expectRevert(InferenceLog.ZeroValue.selector);
        ilog.commit(A, Q, bytes32(0), ROOT, MODEL, 1);
        _commit();
        vm.roll(block.number + 1);
        vm.expectRevert(InferenceLog.ZeroValue.selector);
        ilog.finalize(A, bytes32(0));
    }

    function test_onlyLoggerCanWrite() public {
        vm.prank(other);
        vm.expectRevert(InferenceLog.NotLogger.selector);
        ilog.commit(A, Q, REQ, ROOT, MODEL, 5);
        _commit();
        vm.roll(block.number + 1);
        vm.prank(other);
        vm.expectRevert(InferenceLog.NotLogger.selector);
        ilog.finalize(A, RESP);
    }

    function test_ownerManagesLoggers() public {
        vm.prank(other);
        vm.expectRevert(InferenceLog.NotOwner.selector);
        ilog.setLogger(other, true);
        ilog.setLogger(logger2, true);
        vm.prank(logger2);
        ilog.commit(A, Q, REQ, ROOT, MODEL, 5);
        assertEq(ilog.getRecord(A).logger, logger2);
        ilog.setLogger(logger2, false);
        vm.roll(block.number + 1);
        vm.prank(logger2);
        vm.expectRevert(InferenceLog.NotLogger.selector);
        ilog.finalize(A, RESP);
        vm.expectRevert(InferenceLog.ZeroValue.selector);
        ilog.setLogger(address(0), true);
    }

    function test_transferOwnership() public {
        ilog.transferOwnership(other);
        assertEq(ilog.owner(), other);
        vm.expectRevert(InferenceLog.NotOwner.selector);
        ilog.setLogger(logger2, true);
        vm.prank(other);
        vm.expectRevert(InferenceLog.ZeroValue.selector);
        ilog.transferOwnership(address(0));
    }

    function test_unknownRecordIsEmpty() public view {
        InferenceLog.Record memory r = ilog.getRecord(A);
        assertEq(r.commitBlock, 0); assertEq(r.requestHash, bytes32(0));
    }

    function testFuzz_commitFinalize(bytes32 a, bytes32 req, bytes32 resp) public {
        vm.assume(a != 0 && req != 0 && resp != 0);
        ilog.commit(a, Q, req, ROOT, MODEL, 3);
        vm.roll(block.number + 1);
        ilog.finalize(a, resp);
        InferenceLog.Record memory r = ilog.getRecord(a);
        assertEq(r.requestHash, req); assertEq(r.responseHash, resp); assertGt(r.finalizeBlock, r.commitBlock);
    }
}
