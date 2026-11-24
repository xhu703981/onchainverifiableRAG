// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Test} from "forge-std/Test.sol";
import {NewsRegistry} from "../contracts/NewsRegistry.sol";

contract NewsRegistryTest is Test {
    NewsRegistry reg;
    address crawler = makeAddr("crawler");
    address attacker = makeAddr("attacker");
    bytes32 constant R1 = keccak256("batch1");
    bytes32 constant R2 = keccak256("batch2");
    bytes32 constant MH = keccak256("manifest");

    event BatchRegistered(bytes32 indexed root, bytes32 manifestHash, address indexed registrant, uint32 articleCount, uint256 timestamp);

    function setUp() public {
        reg = new NewsRegistry();
        reg.addTrustedPublisher(crawler);
    }

    function test_trustedRegistration() public {
        vm.warp(1_800_000_000);
        vm.expectEmit(true, true, false, true);
        emit BatchRegistered(R1, MH, crawler, 42, 1_800_000_000);
        vm.prank(crawler);
        reg.registerBatch(R1, MH, 42, "https://example/m1.json");
        assertTrue(reg.isRegistered(R1));
        assertTrue(reg.isTrustedRoot(R1));
        (bool ex, bytes32 mh, address r, uint32 n, uint64 ts, string memory uri) = reg.getBatch(R1);
        assertTrue(ex);
        assertEq(mh, MH);
        assertEq(r, crawler);
        assertEq(n, 42);
        assertEq(ts, 1_800_000_000);
        assertEq(uri, "https://example/m1.json");
        assertEq(reg.batchCount(), 1);
        assertEq(reg.roots(0), R1);
    }

    function test_untrustedRegistrant_isRegisteredButNotTrusted() public {
        vm.prank(attacker);
        reg.registerBatch(R1, MH, 3, "x");
        assertTrue(reg.isRegistered(R1));
        assertFalse(reg.isTrustedRoot(R1));
    }

    function test_unregisteredRoot_notTrusted() public view {
        assertFalse(reg.isRegistered(R2));
        assertFalse(reg.isTrustedRoot(R2));
    }

    function test_duplicateRoot_reverts() public {
        vm.startPrank(crawler);
        reg.registerBatch(R1, MH, 1, "x");
        vm.expectRevert(abi.encodeWithSelector(NewsRegistry.RootAlreadyRegistered.selector, R1));
        reg.registerBatch(R1, MH, 1, "x");
        vm.stopPrank();
        vm.prank(attacker); // nobody can overwrite a trusted root
        vm.expectRevert(abi.encodeWithSelector(NewsRegistry.RootAlreadyRegistered.selector, R1));
        reg.registerBatch(R1, keccak256("other"), 1, "y");
    }

    function test_untrustedCannotOverwriteUntrusted() public {
        vm.prank(attacker);
        reg.registerBatch(R1, MH, 1, "x");
        vm.prank(makeAddr("attacker2"));
        vm.expectRevert(abi.encodeWithSelector(NewsRegistry.RootAlreadyRegistered.selector, R1));
        reg.registerBatch(R1, MH, 1, "x");
    }

    function test_trustedReclaimsSquattedRoot() public {
        vm.prank(attacker);
        reg.registerBatch(R1, keccak256("bogus"), 1, "bogus");
        assertFalse(reg.isTrustedRoot(R1));
        vm.prank(crawler);
        reg.registerBatch(R1, MH, 7, "real");
        assertTrue(reg.isTrustedRoot(R1));
        (, bytes32 mh, address r, uint32 n,, string memory uri) = reg.getBatch(R1);
        assertEq(mh, MH);
        assertEq(r, crawler);
        assertEq(n, 7);
        assertEq(uri, "real");
        assertEq(reg.batchCount(), 1);
    }

    function test_revokeUntrustsPastBatches() public {
        vm.prank(crawler);
        reg.registerBatch(R1, MH, 1, "x");
        reg.removeTrustedPublisher(crawler);
        assertFalse(reg.isTrustedRoot(R1));
        assertTrue(reg.isRegistered(R1));
    }

    function test_zeroValues_revert() public {
        vm.startPrank(crawler);
        vm.expectRevert(NewsRegistry.ZeroValue.selector);
        reg.registerBatch(bytes32(0), MH, 1, "x");
        vm.expectRevert(NewsRegistry.ZeroValue.selector);
        reg.registerBatch(R1, bytes32(0), 1, "x");
        vm.expectRevert(NewsRegistry.ZeroValue.selector);
        reg.registerBatch(R1, MH, 0, "x");
        vm.stopPrank();
    }

    function test_onlyOwnerManagesAllowlist() public {
        vm.prank(attacker);
        vm.expectRevert(NewsRegistry.NotOwner.selector);
        reg.addTrustedPublisher(attacker);
        vm.prank(attacker);
        vm.expectRevert(NewsRegistry.NotOwner.selector);
        reg.removeTrustedPublisher(crawler);
    }

    function test_areTrustedRoots() public {
        vm.prank(crawler);
        reg.registerBatch(R1, MH, 1, "x");
        vm.prank(attacker);
        reg.registerBatch(R2, MH, 1, "x");
        bytes32[] memory rs = new bytes32[](3);
        rs[0] = R1; rs[1] = R2; rs[2] = keccak256("none");
        bool[] memory ok = reg.areTrustedRoots(rs);
        assertTrue(ok[0]);
        assertFalse(ok[1]);
        assertFalse(ok[2]);
    }
}
