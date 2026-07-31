#!/usr/bin/env python3
"""Check deployer ETH balance and print funding instructions."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eth_account import Account
from web3 import Web3

from config.settings import load_settings

DEPLOYER_HINT_ETH = 0.002


def main() -> None:
    settings = load_settings("base")
    pk = os.getenv("DEPLOYER_PRIVATE_KEY") or os.getenv("PRIVATE_KEY_2")
    if not pk:
        print("Set DEPLOYER_PRIVATE_KEY or PRIVATE_KEY_2")
        sys.exit(1)
    if not pk.startswith("0x"):
        pk = f"0x{pk}"
    acct = Account.from_key(pk)
    w3 = Web3(Web3.HTTPProvider(settings.rpc_url))
    bal = w3.eth.get_balance(acct.address)
    print(f"Deployer: {acct.address}")
    print(f"Balance:  {bal / 1e18:.8f} ETH")
    print(f"Need:     ~{DEPLOYER_HINT_ETH:.4f} ETH on Base for contract deployment")
    if bal / 1e18 < DEPLOYER_HINT_ETH:
        print("\nFund the deployer EOA with Base ETH, then run:")
        print("  python scripts/deploy_contract.py --moonwell")
        print("  python scripts/deploy_contract.py --moonwell-oev")
        sys.exit(2)
    print("\nReady to deploy.")
    sys.exit(0)


if __name__ == "__main__":
    main()
