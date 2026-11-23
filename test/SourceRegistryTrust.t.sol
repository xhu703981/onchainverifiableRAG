// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Test} from "forge-std/Test.sol";
import {SourceRegistry} from "../contracts/SourceRegistry.sol";

contract SourceRegistryTrustTest is Test {
    SourceRegistry public registry;
    address public owner = address(this);
    address public publisher = makeAddr("publisher");
    address public attacker = makeAddr("attacker");

    bytes32 constant H1 = keccak256("p1");
    bytes32 constant H2 = keccak256("p2");
    bytes32 constant H3 = keccak256("p3");
    // sha256("hello world"), CID cross-checked with kubo 0.32.1 `ipfs add --only-hash --cid-version 1`
    bytes32 constant HW = 0xb94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9;
    string constant HW_CID = "bafkreifzjut3te2nhyekklss27nh3k72ysco7y32koao5eei66wof36n5e";

    event TrustedPublisherAdded(address indexed publisher);
    event TrustedPublisherRemoved(address indexed publisher);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    event BatchRegistered(address indexed registrant, uint256 submitted, uint256 added, uint256 timestamp);

    function setUp() public {
        registry = new SourceRegistry();
    }

    function _arr(bytes32 a, bytes32 b) internal pure returns (bytes32[] memory x) {
        x = new bytes32[](2);
        x[0] = a;
        x[1] = b;
    }

    function test_ownerIsDeployer() public view {
        assertEq(registry.owner(), owner);
    }

    function test_addRemoveTrustedPublisher_eventsAndViews() public {
        vm.expectEmit(true, false, false, false);
        emit TrustedPublisherAdded(publisher);
        registry.addTrustedPublisher(publisher);
        assertTrue(registry.isTrustedPublisher(publisher));

        vm.expectEmit(true, false, false, false);
        emit TrustedPublisherRemoved(publisher);
        registry.removeTrustedPublisher(publisher);
        assertFalse(registry.isTrustedPublisher(publisher));
    }

    function test_onlyOwnerCanManageTrust() public {
        vm.startPrank(attacker);
        vm.expectRevert(SourceRegistry.NotOwner.selector);
        registry.addTrustedPublisher(attacker);
        vm.expectRevert(SourceRegistry.NotOwner.selector);
        registry.removeTrustedPublisher(publisher);
        vm.expectRevert(SourceRegistry.NotOwner.selector);
        registry.transferOwnership(attacker);
        vm.stopPrank();
    }

    function test_addTrustedPublisher_revertZero() public {
        vm.expectRevert(SourceRegistry.ZeroAddress.selector);
        registry.addTrustedPublisher(address(0));
    }

    function test_transferOwnership() public {
        address newOwner = makeAddr("newOwner");
        vm.expectEmit(true, true, false, false);
        emit OwnershipTransferred(owner, newOwner);
        registry.transferOwnership(newOwner);
        assertEq(registry.owner(), newOwner);
        vm.expectRevert(SourceRegistry.NotOwner.selector);
        registry.addTrustedPublisher(publisher);
        vm.prank(newOwner);
        registry.addTrustedPublisher(publisher);
        assertTrue(registry.isTrustedPublisher(publisher));
        vm.expectRevert(SourceRegistry.ZeroAddress.selector);
        vm.prank(newOwner);
        registry.transferOwnership(address(0));
    }

    /// The hole: an attacker can register, so isRegistered alone passes poison.
    function test_attackerRegistered_isRegisteredButNotVerified() public {
        registry.addTrustedPublisher(publisher);
        vm.prank(attacker);
        registry.registerSource(H1, "cid", "uri");
        vm.prank(attacker);
        registry.registerBatch(_arr(H2, H3));

        assertTrue(registry.isRegistered(H1));
        assertTrue(registry.isRegistered(H2));
        assertFalse(registry.isVerified(H1));
        assertFalse(registry.isVerified(H2));
        assertFalse(registry.isVerified(H3));
    }

    function test_isVerified_singleAndBatch_andRevocation() public {
        registry.addTrustedPublisher(publisher);
        vm.prank(publisher);
        registry.registerSource(H1, "cid", "uri");
        vm.prank(publisher);
        registry.registerBatch(_arr(H2, H3));
        assertTrue(registry.isVerified(H1));
        assertTrue(registry.isVerified(H2));
        assertTrue(registry.isVerified(H3));

        registry.removeTrustedPublisher(publisher);
        assertFalse(registry.isVerified(H1));
        assertFalse(registry.isVerified(H2));
        assertTrue(registry.isRegistered(H2));
    }

    function test_isVerified_unknownHash() public view {
        assertFalse(registry.isVerified(H1));
        assertEq(registry.registrantOf(H1), address(0));
    }

    function test_registerBatch_skipsDuplicates_andCounts() public {
        vm.warp(1_800_000_000);
        vm.expectEmit(true, false, false, true);
        emit BatchRegistered(publisher, 2, 2, 1_800_000_000);
        vm.prank(publisher);
        uint256 added = registry.registerBatch(_arr(H1, H2));
        assertEq(added, 2);

        // H2 already batch-registered; H1 duplicate within same call is also skipped.
        vm.prank(attacker);
        added = registry.registerBatch(_arr(H2, H2));
        assertEq(added, 0);
        assertEq(registry.registrantOf(H2), publisher);

        assertEq(registry.batchSourceCount(), 2);
        assertEq(registry.totalSources(), 0); // single-path counter unchanged
        assertEq(registry.totalAllSources(), 2);
        (address r, uint64 ts) = registry.batchSources(H1);
        assertEq(r, publisher);
        assertEq(ts, 1_800_000_000);
    }

    function test_registerBatch_inBatchDuplicateCountedOnce() public {
        bytes32[] memory x = new bytes32[](3);
        x[0] = H1; x[1] = H1; x[2] = H2;
        vm.prank(publisher);
        assertEq(registry.registerBatch(x), 2);
    }

    function test_registerSource_revertsIfBatchRegistered() public {
        vm.prank(publisher);
        registry.registerBatch(_arr(H1, H2));
        vm.prank(attacker);
        vm.expectRevert(abi.encodeWithSelector(SourceRegistry.SourceAlreadyRegistered.selector, H1));
        registry.registerSource(H1, "cid", "uri");
    }

    function test_registerBatch_skipsSinglePathHash() public {
        vm.prank(publisher);
        registry.registerSource(H1, "cid", "uri");
        vm.prank(attacker);
        assertEq(registry.registerBatch(_arr(H1, H2)), 1);
        assertEq(registry.registrantOf(H1), publisher);
    }

    function test_getSource_batchDerivesCid() public {
        vm.warp(1_800_000_123);
        bytes32[] memory x = new bytes32[](1);
        x[0] = HW;
        vm.prank(publisher);
        registry.registerBatch(x);
        (bool exists, string memory cid, string memory uri, address r, uint256 ts) = registry.getSource(HW);
        assertTrue(exists);
        assertEq(cid, HW_CID);
        assertEq(uri, "");
        assertEq(r, publisher);
        assertEq(ts, 1_800_000_123);
    }

    function test_cidOf_matchesKubo() public view {
        assertEq(registry.cidOf(HW), HW_CID);
        assertEq(bytes(registry.cidOf(H1)).length, 59);
    }

    function test_updateSource_batchHashNotFound() public {
        vm.prank(publisher);
        registry.registerBatch(_arr(H1, H2));
        vm.prank(publisher);
        vm.expectRevert(abi.encodeWithSelector(SourceRegistry.SourceNotFound.selector, H1));
        registry.updateSource(H1, "cid", "uri");
    }

    function test_areVerified_and_areRegistered() public {
        registry.addTrustedPublisher(publisher);
        vm.prank(publisher);
        registry.registerBatch(_arr(H1, H1));
        vm.prank(attacker);
        registry.registerBatch(_arr(H2, H2));
        bytes32[] memory q = new bytes32[](3);
        q[0] = H1; q[1] = H2; q[2] = H3;
        bool[] memory v = registry.areVerified(q);
        bool[] memory g = registry.areRegistered(q);
        assertTrue(v[0]); assertFalse(v[1]); assertFalse(v[2]);
        assertTrue(g[0]); assertTrue(g[1]); assertFalse(g[2]);
    }

    function testFuzz_verifiedIffTrustedRegistrant(bytes32 h, address who, bool trust) public {
        vm.assume(who != address(0));
        if (trust) registry.addTrustedPublisher(who);
        bytes32[] memory x = new bytes32[](1);
        x[0] = h;
        vm.prank(who);
        registry.registerBatch(x);
        assertTrue(registry.isRegistered(h));
        assertEq(registry.isVerified(h), trust);
    }
}
