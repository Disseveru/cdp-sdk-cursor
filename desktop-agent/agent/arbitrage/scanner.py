"""Scan cross-venue arbitrage; validate only via on-chain simulation."""

from __future__ import annotations

import logging

import aiohttp

from agent.arbitrage.kyber_swap import build_swap, fetch_route
from agent.arbitrage.models import ArbitrageOpportunity
from agent.arbitrage.uni_swap import FEE_TIERS, build_uni_exact_input_single
from agent.cdp_wallet import CdpWalletManager
from agent.protocols.aave_v3_base import CBETH_BASE, USDC_BASE, WETH_BASE
from config.settings import AgentSettings

logger = logging.getLogger(__name__)

AMOUNTS_USDC = [10_000_000, 50_000_000, 100_000_000, 200_000_000, 500_000_000, 1_000_000_000]


async def scan_simulated_opportunities(
    settings: AgentSettings,
    wallet: CdpWalletManager,
    arb_contract: str,
    profit_recipient: str,
    *,
    slippage_bps: int = 30,
) -> list[ArbitrageOpportunity]:
    """Try Uni↔Kyber and Uni↔Uni routes; keep only those that pass eth_call simulation."""
    from agent.arbitrage.executor import FlashArbitrageExecutor

    executor = FlashArbitrageExecutor(settings, wallet, arb_contract)
    w3 = wallet.bundle.w3
    smart_account = wallet.bundle.smart_account.address
    opportunities: list[ArbitrageOpportunity] = []

    async with aiohttp.ClientSession() as session:
        for amount in AMOUNTS_USDC:
            await _scan_kyber_kyber(
                session, executor, w3, smart_account, profit_recipient,
                arb_contract, amount, slippage_bps, opportunities,
            )
            await _scan_uni_kyber(
                session, executor, w3, smart_account, profit_recipient,
                arb_contract, USDC_BASE, WETH_BASE, amount, slippage_bps, opportunities,
            )
            await _scan_kyber_uni(
                session, executor, w3, smart_account, profit_recipient,
                arb_contract, USDC_BASE, WETH_BASE, amount, slippage_bps, opportunities,
            )
            await _scan_uni_uni(
                executor, w3, smart_account, profit_recipient,
                arb_contract, USDC_BASE, WETH_BASE, amount, opportunities,
            )
            await _scan_uni_kyber(
                session, executor, w3, smart_account, profit_recipient,
                arb_contract, USDC_BASE, CBETH_BASE, amount, slippage_bps, opportunities,
            )

    opportunities.sort(key=lambda o: o.expected_profit_usd, reverse=True)
    return opportunities


async def _scan_kyber_kyber(session, executor, w3, sa, recipient, arb, amount, slip, out):
    leg1 = await build_swap(USDC_BASE, WETH_BASE, amount, arb, slippage_bps=slip, session=session)
    if not leg1:
        return
    leg2 = await build_swap(
        WETH_BASE, USDC_BASE, int(leg1.amount_out * 98 // 100), arb, slippage_bps=slip, session=session
    )
    if not leg2:
        return
    opp = _opp(USDC_BASE, WETH_BASE, amount, leg1.router, leg1.calldata, leg2.router, leg2.calldata, "kyber->kyber")
    if _simulates(executor, w3, sa, opp, recipient):
        opp.expected_profit_raw = leg2.amount_out - amount
        opp.expected_profit_usd = opp.expected_profit_raw / 1_000_000
        out.append(opp)
        logger.info("Sim OK kyber->kyber %s USDC profit=$%.4f", amount / 1e6, opp.expected_profit_usd)


async def _scan_uni_kyber(session, executor, w3, sa, recipient, arb, token_in, token_out, amount, slip, out):
    route = await fetch_route(token_in, token_out, amount, session=session)
    if not route:
        return
    est_mid = int(route["routeSummary"]["amountOut"])
    for fee in FEE_TIERS:
        uni_router, uni_data = build_uni_exact_input_single(
            w3, token_in=token_in, token_out=token_out, fee=fee, recipient=arb, amount_in=amount
        )
        leg2 = await build_swap(token_out, token_in, int(est_mid * 98 // 100), arb, slippage_bps=slip, session=session)
        if not leg2:
            continue
        opp = _opp(token_in, token_out, amount, uni_router, uni_data, leg2.router, leg2.calldata, f"uni{fee}->kyber")
        if _simulates(executor, w3, sa, opp, recipient):
            opp.expected_profit_raw = leg2.amount_out - amount
            opp.expected_profit_usd = opp.expected_profit_raw / 1_000_000
            out.append(opp)
            logger.info("Sim OK %s profit=$%.4f", opp.route_label, opp.expected_profit_usd)


async def _scan_kyber_uni(session, executor, w3, sa, recipient, arb, token_in, token_out, amount, slip, out):
    leg1 = await build_swap(token_in, token_out, amount, arb, slippage_bps=slip, session=session)
    if not leg1:
        return
    for fee in FEE_TIERS:
        uni_router, uni_data = build_uni_exact_input_single(
            w3, token_in=token_out, token_out=token_in, fee=fee, recipient=arb, amount_in=leg1.amount_out
        )
        opp = _opp(token_in, token_out, amount, leg1.router, leg1.calldata, uni_router, uni_data, f"kyber->uni{fee}")
        if _simulates(executor, w3, sa, opp, recipient):
            opp.expected_profit_raw = 1
            opp.expected_profit_usd = 0.01
            out.append(opp)
            logger.info("Sim OK %s", opp.route_label)


async def _scan_uni_uni(executor, w3, sa, recipient, arb, token_in, token_out, amount, out):
    for fee1 in FEE_TIERS:
        for fee2 in FEE_TIERS:
            if fee1 == fee2:
                continue
            r1, d1 = build_uni_exact_input_single(
                w3, token_in=token_in, token_out=token_out, fee=fee1, recipient=arb, amount_in=amount
            )
            route = None
            try:
                import asyncio
                async with aiohttp.ClientSession() as session:
                    route = await fetch_route(token_in, token_out, amount, session=session)
            except Exception:
                pass
            est = int(route["routeSummary"]["amountOut"]) if route else amount
            r2, d2 = build_uni_exact_input_single(
                w3, token_in=token_out, token_out=token_in, fee=fee2, recipient=arb, amount_in=est
            )
            opp = _opp(token_in, token_out, amount, r1, d1, r2, d2, f"uni{fee1}->uni{fee2}")
            if _simulates(executor, w3, sa, opp, recipient):
                opp.expected_profit_raw = 1
                opp.expected_profit_usd = 0.01
                out.append(opp)
                logger.info("Sim OK %s", opp.route_label)


def _opp(loan, mid, amount, r1, d1, r2, d2, label) -> ArbitrageOpportunity:
    return ArbitrageOpportunity(
        loan_token=loan,
        intermediate_token=mid,
        loan_symbol="USDC",
        intermediate_symbol="WETH" if mid == WETH_BASE else "cbETH",
        amount_in=amount,
        expected_profit_raw=0,
        expected_profit_usd=0,
        leg1_router=r1,
        leg1_calldata=d1,
        leg2_router=r2,
        leg2_calldata=d2,
        route_label=label,
    )


def _simulates(executor, w3, smart_account, opp, profit_recipient) -> bool:
    try:
        data = executor.encode_execute(opp, profit_recipient, min_profit=1)
        w3.eth.call({"from": smart_account, "to": executor.contract, "data": data})
        return True
    except Exception:
        return False
