// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Test} from "forge-std/Test.sol";
import {CorpusRegistry} from "../contracts/CorpusRegistry.sol";

contract CorpusRegistryTest is Test {
    CorpusRegistry reg;
    address alice = address(0xA11CE);
    bytes32 constant ROOT = keccak256("root");
    bytes32 constant CFG = keccak256("cfg");

    event CorpusPublished(uint256 indexed version, bytes32 indexed root, bytes32 configHash, uint32 leafCount,
        string uri, address indexed publisher);

    function setUp() public {
        reg = new CorpusRegistry();
    }

    function test_ownerIsPublisher() public view {
        assertEq(reg.owner(), address(this));
        assertTrue(reg.isPublisher(address(this)));
        assertEq(reg.latestVersion(), 0);
    }

    function test_publishAndRead() public {
        vm.roll(42);
        vm.warp(1_700_000_000);
        vm.expectEmit(true, true, true, true);
        emit CorpusPublished(1, ROOT, CFG, 1998, "ipfs://x", address(this));
        uint256 v = reg.publish(ROOT, 1998, CFG, "ipfs://x");
        assertEq(v, 1);
        (bytes32 r, bytes32 c, uint32 n, address p, uint64 bn, uint64 ts) = reg.corpusInfo(1);
        assertEq(r, ROOT); assertEq(c, CFG); assertEq(n, 1998); assertEq(p, address(this));
        assertEq(bn, 42); assertEq(ts, 1_700_000_000);
        assertEq(reg.corpusUri(1), "ipfs://x");
        assertEq(reg.publish(keccak256("r2"), 5, CFG, ""), 2);
        assertEq(reg.latestVersion(), 2);
        (r,,,,,) = reg.corpusInfo(1);
        assertEq(r, ROOT, "versions are append-only");
    }

    function test_onlyPublisher() public {
        vm.prank(alice);
        vm.expectRevert(CorpusRegistry.NotPublisher.selector);
        reg.publish(ROOT, 1, CFG, "");
        reg.setPublisher(alice, true);
        vm.prank(alice);
        assertEq(reg.publish(ROOT, 1, CFG, ""), 1);
        reg.setPublisher(alice, false);
        vm.prank(alice);
        vm.expectRevert(CorpusRegistry.NotPublisher.selector);
        reg.publish(ROOT, 1, CFG, "");
    }

    function test_onlyOwnerAdmin() public {
        vm.prank(alice);
        vm.expectRevert(CorpusRegistry.NotOwner.selector);
        reg.setPublisher(alice, true);
        vm.prank(alice);
        vm.expectRevert(CorpusRegistry.NotOwner.selector);
        reg.transferOwnership(alice);
        reg.transferOwnership(alice);
        assertEq(reg.owner(), alice);
    }

    function test_rejectsZero() public {
        vm.expectRevert(CorpusRegistry.ZeroValue.selector);
        reg.publish(bytes32(0), 1, CFG, "");
        vm.expectRevert(CorpusRegistry.ZeroValue.selector);
        reg.publish(ROOT, 0, CFG, "");
        vm.expectRevert(CorpusRegistry.ZeroValue.selector);
        reg.publish(ROOT, 1, bytes32(0), "");
    }

    function test_noSuchVersion() public {
        vm.expectRevert(abi.encodeWithSelector(CorpusRegistry.NoSuchVersion.selector, 0));
        reg.corpusInfo(0);
        vm.expectRevert(abi.encodeWithSelector(CorpusRegistry.NoSuchVersion.selector, 1));
        reg.corpusInfo(1);
    }

    function testFuzz_versionsIncrement(uint8 n) public {
        for (uint256 i = 1; i <= n; i++) {
            assertEq(reg.publish(bytes32(i), uint32(i), CFG, ""), i);
        }
        assertEq(reg.latestVersion(), n);
    }
}
