#!/usr/bin/env python3
"""Scan Base mainnet and execute the best profitable liquidation immediately."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.ai_engine import LiquidationAIEngine
from agent.cdp_wallet import CdpWalletManager
from agent.execution_history import ExecutionHistory, ExecutionRecord
from agent.executor import FlashLiquidationExecutor
from agent.scanner import MultiProtocolScanner
from config.settings import load_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("run_liquidation")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Scan and execute liquidations on Base")
    parser.add_argument("--network", choices=["base", "base-sepolia"], default="base")
    parser.add_argument("--scan-only", action="store_true", help="Scan without executing")
    parser.add_argument("--borrower-limit", type=int, default=300)
    args = parser.parse_args()

    settings = load_settings(args.network)
    if not settings.flash_liquidator_address and not settings.morpho_flash_liquidator_address:
        logger.error(
            "Set FLASH_LIQUIDATOR_ADDRESS and/or MORPHO_FLASH_LIQUIDATOR_ADDRESS before running."
        )
        return 1

    wallet = CdpWalletManager(settings)
    exit_code = 1
    try:
        bundle = await wallet.initialize()
        scanner = MultiProtocolScanner(settings, bundle.w3)
        executor = FlashLiquidationExecutor(settings, wallet)
        ai = LiquidationAIEngine(settings)
        history = ExecutionHistory()

        logger.info("Smart account: %s", bundle.smart_account.address)
        logger.info("Protocols: %s", ", ".join(scanner.protocol_names))
        logger.info("Aave liquidator: %s", settings.flash_liquidator_address)
        logger.info("Morpho liquidator: %s", settings.morpho_flash_liquidator_address)

        borrowers = await scanner.discover_borrowers(limit=args.borrower_limit)
        logger.info("Borrowers: %s", scanner.borrower_stats(borrowers))

        targets = await scanner.scan(borrowers)
        executable = [
            t
            for t in targets
            if t.executable and t.estimated_profit_usd >= settings.min_profit_usd
        ]
        logger.info(
            "Found %d liquidatable, %d executable above $%.2f min profit",
            len(targets),
            len(executable),
            settings.min_profit_usd,
        )
        for t in targets[:10]:
            logger.info(
                "  [%s] %s HF=%.4f profit=$%.2f exec=%s %s/%s",
                t.protocol_id,
                t.user,
                t.health_factor,
                t.estimated_profit_usd,
                t.executable,
                t.collateral_symbol,
                t.debt_symbol,
            )

        decision = await ai.decide(targets)
        logger.info("Decision: %s | %s", decision.action, decision.reasoning)

        if args.scan_only or decision.action != "execute" or not decision.target_user:
            exit_code = 0 if targets else 2
            return exit_code

        match = next(
            (
                t
                for t in targets
                if t.user.lower() == decision.target_user.lower()
                and t.protocol_id == decision.protocol_id
                and t.executable
            ),
            None,
        )
        if match is None:
            logger.warning("Decision target not found in executable list")
            exit_code = 3
            return exit_code

        logger.info("Executing liquidation for %s on %s", match.user, match.protocol_name)
        result = await executor.execute(match)
        history.append(ExecutionRecord.from_execution_result(result))
        logger.info("Status: %s", result.status)
        logger.info("Message: %s", result.message)
        if result.user_op_hash:
            logger.info("UserOp: %s", result.user_op_hash)
        if result.tx_hash:
            logger.info("Tx: https://basescan.org/tx/%s", result.tx_hash)
        logger.info("Recorded execution #%d", history.summary()["total"])
        exit_code = 0 if result.status in ("complete", "success", "confirmed") else 4
        return exit_code
    finally:
        await wallet.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
