#!/usr/bin/env python3
"""Scan and immediately execute flash arbitrage; profits to EOA."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.arbitrage.executor import FlashArbitrageExecutor
from agent.arbitrage.kyber_swap import build_swap
from agent.arbitrage.models import ArbitrageOpportunity
from agent.cdp_wallet import CdpWalletManager
from agent.protocols.aave_v3_base import USDC_BASE, WETH_BASE
from config.settings import load_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("run_arbitrage")

HISTORY_PATH = ROOT / "data" / "arbitrage_executions.json"
AMOUNTS = [30_000_000, 50_000_000, 75_000_000, 100_000_000]


async def try_kyber_roundtrip(
    arb: str, amount: int, slip: int, session, eoa: str, executor, w3, sa
) -> ArbitrageOpportunity | None:
    leg1 = await build_swap(USDC_BASE, WETH_BASE, amount, arb, slippage_bps=slip, session=session)
    if not leg1:
        return None
    leg2_in = int(leg1.amount_out * 95 // 100)
    leg2 = await build_swap(WETH_BASE, USDC_BASE, leg2_in, arb, slippage_bps=slip, session=session)
    if not leg2:
        return None
    opp = ArbitrageOpportunity(
        loan_token=USDC_BASE,
        intermediate_token=WETH_BASE,
        loan_symbol="USDC",
        intermediate_symbol="WETH",
        amount_in=amount,
        expected_profit_raw=leg2.amount_out - amount,
        expected_profit_usd=(leg2.amount_out - amount) / 1_000_000,
        leg1_router=leg1.router,
        leg1_calldata=leg1.calldata,
        leg2_router=leg2.router,
        leg2_calldata=leg2.calldata,
        route_label="kyber->kyber",
    )
    try:
        data = executor.encode_execute(opp, eoa, min_profit=1)
        w3.eth.call({"from": sa, "to": executor.contract, "data": data})
        return opp
    except Exception as exc:
        logger.debug("Sim fail %s USDC: %s", amount / 1e6, exc)
        return None


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scan-only", action="store_true")
    args = parser.parse_args()

    settings = load_settings("base")
    arb = os.getenv("FLASH_ARBITRAGE_ADDRESS")
    if not arb:
        logger.error("Set FLASH_ARBITRAGE_ADDRESS")
        return 1

    wallet = CdpWalletManager(settings)
    bundle = await wallet.initialize()
    eoa = bundle.owner.address
    sa = bundle.smart_account.address
    executor = FlashArbitrageExecutor(settings, wallet, arb)

    logger.info("EOA profit recipient: %s", eoa)
    logger.info("Arb contract: %s", arb)

    import aiohttp

    best: ArbitrageOpportunity | None = None
    async with aiohttp.ClientSession() as session:
        for amount in AMOUNTS:
            for slip in (20, 30, 50):
                opp = await try_kyber_roundtrip(arb, amount, slip, session, eoa, executor, bundle.w3, sa)
                if opp and (best is None or opp.expected_profit_usd > best.expected_profit_usd):
                    best = opp
                    logger.info(
                        "Candidate: %s USDC profit=$%.4f slip=%sbps",
                        amount / 1e6,
                        opp.expected_profit_usd,
                        slip,
                    )

    if best is None:
        logger.info("No sim-passing Kyber round-trip arbitrage on Base right now")
        await wallet.close()
        return 2

    logger.info("Best: $%.4f profit on %s USDC", best.expected_profit_usd, best.amount_in / 1e6)
    if args.scan_only:
        await wallet.close()
        return 0

    result = await executor.execute(best, eoa, min_profit=1, simulate=False)
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "status": result.status,
        "message": result.message,
        "user_op_hash": result.user_op_hash,
        "tx_hash": result.tx_hash,
        "profit_recipient": eoa,
        "expected_profit_usd": best.expected_profit_usd,
        "amount_in": best.amount_in,
        "route": best.route_label,
    }
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    records = json.loads(HISTORY_PATH.read_text()) if HISTORY_PATH.exists() else []
    records.insert(0, record)
    HISTORY_PATH.write_text(json.dumps(records[:100], indent=2))

    logger.info("Status: %s", result.status)
    logger.info("Message: %s", result.message)
    if result.tx_hash:
        logger.info("https://basescan.org/tx/%s", result.tx_hash)

    await wallet.close()
    return 0 if result.status in ("complete", "success", "confirmed") else 3


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
