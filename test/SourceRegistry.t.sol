// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Test} from "forge-std/Test.sol";
import {SourceRegistry} from "../contracts/SourceRegistry.sol";

contract SourceRegistryTest is Test {
    SourceRegistry public registry;

    address public registrant = makeAddr("registrant");
    address public other = makeAddr("other");

    bytes32 public constant HASH_A = keccak256("doc-a");
    bytes32 public constant HASH_B = keccak256("doc-b");
    string public constant CID_A = "QmCidA";
    string public constant CID_B = "QmCidB";
    string public constant URI_A = "ipfs://QmCidA";
    string public constant URI_B = "https://example.com/doc-b";

    event SourceRegistered(bytes32 indexed contentHash, string cid, address indexed registrant, uint256 timestamp);
    event SourceUpdated(bytes32 indexed contentHash, string cid, address indexed registrant);

    function setUp() public {
        registry = new SourceRegistry();
    }

    function test_registerSource_successAndEvent() public {
        vm.warp(1_700_000_000);

        vm.expectEmit(true, true, false, true);
        emit SourceRegistered(HASH_A, CID_A, registrant, block.timestamp);

        vm.prank(registrant);
        registry.registerSource(HASH_A, CID_A, URI_A);

        (bool exists, string memory cid, string memory sourceUri, address recRegistrant, uint256 timestamp) =
            registry.getSource(HASH_A);
        assertTrue(exists);
        assertEq(cid, CID_A);
        assertEq(sourceUri, URI_A);
        assertEq(recRegistrant, registrant);
        assertEq(timestamp, 1_700_000_000);
        assertTrue(registry.isRegistered(HASH_A));
        assertEq(registry.totalSources(), 1);
        assertEq(registry.allHashes(0), HASH_A);

        (bytes32 contentHash, string memory storedCid, string memory storedUri, address storedReg, uint256 storedTs) =
            registry.sources(HASH_A);
        assertEq(contentHash, HASH_A);
        assertEq(storedCid, CID_A);
        assertEq(storedUri, URI_A);
        assertEq(storedReg, registrant);
        assertEq(storedTs, 1_700_000_000);
    }

    function test_registerSource_revertEmptyCID() public {
        vm.prank(registrant);
        vm.expectRevert(SourceRegistry.EmptyCID.selector);
        registry.registerSource(HASH_A, "", URI_A);

        assertFalse(registry.isRegistered(HASH_A));
        assertEq(registry.totalSources(), 0);
    }

    function test_registerSource_revertAlreadyRegistered() public {
        vm.prank(registrant);
        registry.registerSource(HASH_A, CID_A, URI_A);

        vm.prank(other);
        vm.expectRevert(abi.encodeWithSelector(SourceRegistry.SourceAlreadyRegistered.selector, HASH_A));
        registry.registerSource(HASH_A, CID_B, URI_B);

        assertEq(registry.totalSources(), 1);
    }

    function test_updateSource_byRegistrantAndEvent() public {
        vm.prank(registrant);
        registry.registerSource(HASH_A, CID_A, URI_A);

        vm.expectEmit(true, true, false, true);
        emit SourceUpdated(HASH_A, CID_B, registrant);

        vm.prank(registrant);
        registry.updateSource(HASH_A, CID_B, URI_B);

        (bool exists, string memory cid, string memory sourceUri, address recRegistrant, uint256 timestamp) =
            registry.getSource(HASH_A);
        assertTrue(exists);
        assertEq(cid, CID_B);
        assertEq(sourceUri, URI_B);
        assertEq(recRegistrant, registrant);
        assertGt(timestamp, 0);
        assertEq(registry.totalSources(), 1);
        assertEq(registry.allHashes(0), HASH_A);
    }

    function test_updateSource_revertNonRegistrant() public {
        vm.prank(registrant);
        registry.registerSource(HASH_A, CID_A, URI_A);

        vm.prank(other);
        vm.expectRevert(bytes("Only registrant can update"));
        registry.updateSource(HASH_A, CID_B, URI_B);

        (, string memory cid, string memory sourceUri,,) = registry.getSource(HASH_A);
        assertEq(cid, CID_A);
        assertEq(sourceUri, URI_A);
    }

    function test_updateSource_revertSourceNotFound() public {
        vm.prank(registrant);
        vm.expectRevert(abi.encodeWithSelector(SourceRegistry.SourceNotFound.selector, HASH_A));
        registry.updateSource(HASH_A, CID_A, URI_A);
    }

    function test_updateSource_revertEmptyCID() public {
        vm.prank(registrant);
        registry.registerSource(HASH_A, CID_A, URI_A);

        vm.prank(registrant);
        vm.expectRevert(SourceRegistry.EmptyCID.selector);
        registry.updateSource(HASH_A, "", URI_B);

        (, string memory cid, string memory sourceUri,,) = registry.getSource(HASH_A);
        assertEq(cid, CID_A);
        assertEq(sourceUri, URI_A);
    }

    function test_getSource_existingAndMissing() public {
        vm.prank(registrant);
        registry.registerSource(HASH_A, CID_A, URI_A);

        (bool existsA, string memory cidA, string memory uriA, address regA, uint256 tsA) = registry.getSource(HASH_A);
        assertTrue(existsA);
        assertEq(cidA, CID_A);
        assertEq(uriA, URI_A);
        assertEq(regA, registrant);
        assertEq(tsA, block.timestamp);

        (bool existsB, string memory cidB, string memory uriB, address regB, uint256 tsB) = registry.getSource(HASH_B);
        assertFalse(existsB);
        assertEq(cidB, "");
        assertEq(uriB, "");
        assertEq(regB, address(0));
        assertEq(tsB, 0);
    }

    function test_isRegistered() public {
        assertFalse(registry.isRegistered(HASH_A));

        vm.prank(registrant);
        registry.registerSource(HASH_A, CID_A, URI_A);

        assertTrue(registry.isRegistered(HASH_A));
        assertFalse(registry.isRegistered(HASH_B));
    }

    function test_totalSourcesAndAllHashes() public {
        assertEq(registry.totalSources(), 0);

        vm.prank(registrant);
        registry.registerSource(HASH_A, CID_A, URI_A);
        assertEq(registry.totalSources(), 1);
        assertEq(registry.allHashes(0), HASH_A);

        vm.prank(other);
        registry.registerSource(HASH_B, CID_B, URI_B);
        assertEq(registry.totalSources(), 2);
        assertEq(registry.allHashes(0), HASH_A);
        assertEq(registry.allHashes(1), HASH_B);
    }

    function test_timestampViaWarp() public {
        uint256 t1 = 1_600_000_000;
        vm.warp(t1);
        vm.prank(registrant);
        registry.registerSource(HASH_A, CID_A, URI_A);

        (,,,, uint256 tsA) = registry.getSource(HASH_A);
        assertEq(tsA, t1);

        uint256 t2 = 1_800_000_000;
        vm.warp(t2);
        vm.prank(other);
        registry.registerSource(HASH_B, CID_B, URI_B);

        (,,,, uint256 tsB) = registry.getSource(HASH_B);
        assertEq(tsB, t2);

        // update must not change the original registration timestamp
        vm.prank(registrant);
        registry.updateSource(HASH_A, CID_B, URI_B);
        (,,,, uint256 tsAAfter) = registry.getSource(HASH_A);
        assertEq(tsAAfter, t1);
    }

    function testFuzz_registerSource(bytes32 contentHash, string calldata cid, string calldata sourceUri, address caller)
        public
    {
        vm.assume(bytes(cid).length > 0);
        vm.assume(caller != address(0));

        uint256 beforeCount = registry.totalSources();

        vm.prank(caller);
        registry.registerSource(contentHash, cid, sourceUri);

        assertTrue(registry.isRegistered(contentHash));
        assertEq(registry.totalSources(), beforeCount + 1);
        assertEq(registry.allHashes(beforeCount), contentHash);

        (bool exists, string memory storedCid, string memory storedUri, address storedReg, uint256 timestamp) =
            registry.getSource(contentHash);
        assertTrue(exists);
        assertEq(storedCid, cid);
        assertEq(storedUri, sourceUri);
        assertEq(storedReg, caller);
        assertEq(timestamp, block.timestamp);
    }

    function testFuzz_registerSource_revertEmptyCID(bytes32 contentHash, string calldata sourceUri, address caller)
        public
    {
        vm.assume(caller != address(0));

        vm.prank(caller);
        vm.expectRevert(SourceRegistry.EmptyCID.selector);
        registry.registerSource(contentHash, "", sourceUri);
    }
}
