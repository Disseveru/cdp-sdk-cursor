#!/usr/bin/env python3
"""Deploy MorphoFlashArbitrage to Base mainnet."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eth_account import Account
from web3 import Web3

from agent.cdp_wallet import CdpWalletManager
from agent.protocols.morpho_base import MORPHO_BLUE_BASE
from config.settings import load_settings

CONTRACTS_DIR = ROOT / "contracts"
ENV_KEY = "FLASH_ARBITRAGE_ADDRESS"


def compile_contracts() -> None:
    subprocess.run(["forge", "build"], cwd=CONTRACTS_DIR, check=True)


def load_artifact() -> dict:
    path = CONTRACTS_DIR / "out" / "MorphoFlashArbitrage.sol" / "MorphoFlashArbitrage.json"
    return json.loads(path.read_text())


def _write_env(contract_address: str) -> None:
    env_path = ROOT / ".env"
    line = f"{ENV_KEY}={contract_address}\n"
    if env_path.exists() and ENV_KEY in env_path.read_text():
        lines = [
            line.strip() if l.startswith(ENV_KEY) else l for l in env_path.read_text().splitlines()
        ]
        env_path.write_text("\n".join(lines) + "\n")
    else:
        env_path.write_text((env_path.read_text() if env_path.exists() else "") + line)


def deploy_with_funded_eoa(settings, artifact: dict, owner: str) -> str:
    pk = os.getenv("DEPLOYER_PRIVATE_KEY") or os.getenv("PRIVATE_KEY_2")
    if not pk:
        raise ValueError("Set DEPLOYER_PRIVATE_KEY or PRIVATE_KEY_2 with Base ETH for deployment.")
    if not pk.startswith("0x"):
        pk = f"0x{pk}"

    acct = Account.from_key(pk)
    w3 = Web3(Web3.HTTPProvider(settings.rpc_url))
    constructor_args = [
        Web3.to_checksum_address(MORPHO_BLUE_BASE),
        Web3.to_checksum_address(owner),
    ]
    contract = w3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"]["object"])
    built = contract.constructor(*constructor_args).build_transaction({"from": acct.address, "value": 0})
    gas = w3.eth.estimate_gas({"from": acct.address, "data": built["data"]})
    block = w3.eth.get_block("latest")
    max_fee = block["baseFeePerGas"] + w3.to_wei(0.001, "gwei")
    balance = w3.eth.get_balance(acct.address)
    cost = gas * max_fee
    if balance < cost:
        raise ValueError(
            f"Deployer {acct.address} needs ~{cost / 1e18:.6f} ETH, has {balance / 1e18:.6f} ETH"
        )

    tx = contract.constructor(*constructor_args).build_transaction(
        {
            "from": acct.address,
            "nonce": w3.eth.get_transaction_count(acct.address),
            "chainId": settings.chain_id,
            "gas": gas,
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": w3.to_wei(0.001, "gwei"),
        }
    )
    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"Deployment tx: {tx_hash.hex()}")

    for _ in range(40):
        try:
            receipt = w3.eth.get_transaction_receipt(tx_hash)
            if receipt.contractAddress:
                return Web3.to_checksum_address(receipt.contractAddress)
        except Exception:
            time.sleep(2)
    raise RuntimeError("Deployment sent but contract address not found.")


async def deploy(network: str | None = None) -> str:
    settings = load_settings(network)
    compile_contracts()
    artifact = load_artifact()
    wallet = CdpWalletManager(settings)
    bundle = await wallet.initialize()
    owner = bundle.smart_account.address

    print(f"Smart Account owner: {owner}")
    print("Deploying: MorphoFlashArbitrage")

    address = deploy_with_funded_eoa(settings, artifact, owner)
    print(f"\nDeployed MorphoFlashArbitrage: {address}")
    print(f"https://basescan.org/address/{address}")
    print("\nAdd contract + executeArbitrage() to CDP Paymaster allowlist.")

    _write_env(address)
    await wallet.close()
    return address


if __name__ == "__main__":
    asyncio.run(deploy())
