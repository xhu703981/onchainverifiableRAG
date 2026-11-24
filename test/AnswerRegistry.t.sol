// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Test} from "forge-std/Test.sol";
import {AnswerRegistry} from "../contracts/AnswerRegistry.sol";

contract AnswerRegistryTest is Test {
    AnswerRegistry reg;
    address alice = address(0xA11CE);
    bytes32 constant ROOT = keccak256("answer");

    event AnswerRecorded(bytes32 indexed root, address indexed recorder, uint256 blockNumber, uint256 timestamp);

    function setUp() public {
        reg = new AnswerRegistry();
    }

    function test_ownerIsRecorder() public view {
        assertEq(reg.owner(), address(this));
        assertTrue(reg.isRecorder(address(this)));
        assertFalse(reg.isRecorder(alice));
    }

    function test_recordAndRead() public {
        vm.roll(73_000_000);
        vm.warp(1_790_000_000);
        vm.expectEmit(true, true, true, true);
        emit AnswerRecorded(ROOT, address(this), 73_000_000, 1_790_000_000);
        reg.record(ROOT);
        (address who, uint64 bn, uint64 ts) = reg.recordOf(ROOT);
        assertEq(who, address(this));
        assertEq(bn, 73_000_000);
        assertEq(ts, 1_790_000_000);
    }

    function test_unknownRootIsZero() public view {
        (address who, uint64 bn, uint64 ts) = reg.recordOf(ROOT);
        assertEq(who, address(0)); assertEq(bn, 0); assertEq(ts, 0);
    }

    function test_rejectsReRecord() public {
        uint64 first = uint64(block.number);
        reg.record(ROOT);
        vm.roll(block.number + 5);
        vm.expectRevert(abi.encodeWithSelector(AnswerRegistry.AlreadyRecorded.selector, ROOT));
        reg.record(ROOT);
        (, uint64 bn,) = reg.recordOf(ROOT);
        assertEq(bn, first, "first record is kept");
    }

    function test_rejectsZeroRoot() public {
        vm.expectRevert(AnswerRegistry.ZeroValue.selector);
        reg.record(bytes32(0));
    }

    function test_onlyRecorder() public {
        vm.prank(alice);
        vm.expectRevert(AnswerRegistry.NotRecorder.selector);
        reg.record(ROOT);
        reg.setRecorder(alice, true);
        vm.prank(alice);
        reg.record(ROOT);
        (address who,,) = reg.recordOf(ROOT);
        assertEq(who, alice);
        reg.setRecorder(alice, false);
        vm.prank(alice);
        vm.expectRevert(AnswerRegistry.NotRecorder.selector);
        reg.record(keccak256("other"));
    }

    function test_onlyOwnerAdmin() public {
        vm.startPrank(alice);
        vm.expectRevert(AnswerRegistry.NotOwner.selector);
        reg.setRecorder(alice, true);
        vm.expectRevert(AnswerRegistry.NotOwner.selector);
        reg.transferOwnership(alice);
        vm.stopPrank();
        vm.expectRevert(AnswerRegistry.ZeroValue.selector);
        reg.setRecorder(address(0), true);
        reg.transferOwnership(alice);
        assertEq(reg.owner(), alice);
    }

    function testFuzz_recordAnyRoot(bytes32 r) public {
        vm.assume(r != bytes32(0));
        reg.record(r);
        (address who,,) = reg.recordOf(r);
        assertEq(who, address(this));
        vm.expectRevert(abi.encodeWithSelector(AnswerRegistry.AlreadyRecorded.selector, r));
        reg.record(r);
    }
}
