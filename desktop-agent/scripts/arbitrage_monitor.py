#!/usr/bin/env python3
"""Continuous flash arbitrage monitor — executes when simulation passes."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import aiohttp

from agent.arbitrage.executor import FlashArbitrageExecutor
from agent.arbitrage.kyber_swap import build_swap
from agent.arbitrage.models import ArbitrageOpportunity
from agent.cdp_wallet import CdpWalletManager
from agent.protocols.aave_v3_base import USDC_BASE, WETH_BASE
from config.settings import load_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("arb_monitor")

HISTORY = ROOT / "data" / "arbitrage_executions.json"
AMOUNTS = [25_000_000, 30_000_000, 40_000_000, 50_000_000, 60_000_000, 75_000_000, 100_000_000]


async def attempt_once(arb, eoa, executor, w3, sa, session, amount, slip) -> ArbitrageOpportunity | None:
    leg1 = await build_swap(USDC_BASE, WETH_BASE, amount, arb, slippage_bps=slip, session=session)
    if not leg1:
        return None
    leg2_in = int(leg1.amount_out * 90 // 100)
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
        route_label=f"kyber->kyber@{slip}bps",
    )
    if opp.expected_profit_raw <= 0:
        return None
    try:
        data = executor.encode_execute(opp, eoa, min_profit=1)
        w3.eth.call({"from": sa, "to": executor.contract, "data": data})
        return opp
    except Exception:
        return None


async def main() -> int:
    settings = load_settings("base")
    arb = os.getenv("FLASH_ARBITRAGE_ADDRESS")
    if not arb:
        logger.error("FLASH_ARBITRAGE_ADDRESS required")
        return 1

    wallet = CdpWalletManager(settings)
    bundle = await wallet.initialize()
    eoa = bundle.owner.address
    sa = bundle.smart_account.address
    executor = FlashArbitrageExecutor(settings, wallet, arb)
    logger.info("Monitoring Base arb | EOA=%s | contract=%s", eoa, arb)

    deadline = time.time() + 600  # 10 min
    attempt = 0
    async with aiohttp.ClientSession() as session:
        while time.time() < deadline:
            attempt += 1
            for amount in AMOUNTS:
                for slip in (15, 25, 40, 60, 80):
                    opp = await attempt_once(arb, eoa, executor, bundle.w3, sa, session, amount, slip)
                    if opp:
                        logger.info(
                            "EXECUTING attempt=%s %s USDC est=$%.4f",
                            attempt,
                            amount / 1e6,
                            opp.expected_profit_usd,
                        )
                        result = await executor.execute(opp, eoa, min_profit=1, simulate=False)
                        record = {
                            "timestamp": datetime.now(UTC).isoformat(),
                            "status": result.status,
                            "message": result.message,
                            "user_op_hash": result.user_op_hash,
                            "tx_hash": result.tx_hash,
                            "profit_recipient": eoa,
                            "expected_profit_usd": opp.expected_profit_usd,
                            "amount_in": opp.amount_in,
                        }
                        HISTORY.parent.mkdir(parents=True, exist_ok=True)
                        recs = json.loads(HISTORY.read_text()) if HISTORY.exists() else []
                        recs.insert(0, record)
                        HISTORY.write_text(json.dumps(recs[:100], indent=2))
                        logger.info("Result: %s %s", result.status, result.message)
                        if result.tx_hash:
                            logger.info("https://basescan.org/tx/%s", result.tx_hash)
                        await wallet.close()
                        return 0 if result.status in ("complete", "success", "confirmed") else 3
            if attempt % 20 == 0:
                logger.info("Still scanning... attempt %s", attempt)
            await asyncio.sleep(2)

    logger.info("No profitable arb found in monitoring window")
    await wallet.close()
    return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
