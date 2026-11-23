// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Script, console} from "forge-std/Script.sol";
import {SourceRegistry} from "../contracts/SourceRegistry.sol";

contract Deploy is Script {
    function run() external {
        vm.startBroadcast();
        SourceRegistry registry = new SourceRegistry();
        vm.stopBroadcast();
        console.log("SourceRegistry deployed at:", address(registry));
    }
}
