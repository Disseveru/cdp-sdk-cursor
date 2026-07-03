"""KyberSwap route building for flash arbitrage execution."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import aiohttp

logger = logging.getLogger(__name__)

KYBER_ROUTES = "https://aggregator-api.kyberswap.com/base/api/v1/routes"
KYBER_BUILD = "https://aggregator-api.kyberswap.com/base/api/v1/route/build"
KYBER_ROUTER_BASE = "0x6131B5fae19EA4f9D964eAc0408E4408b66337b5"
KYBER_CLIENT_ID = "cdp-flash-arbitrage"


@dataclass(frozen=True)
class BuiltSwap:
    token_in: str
    token_out: str
    amount_in: int
    amount_out: int
    router: str
    calldata: str


async def fetch_route(
    token_in: str,
    token_out: str,
    amount_in: int,
    *,
    session: aiohttp.ClientSession,
) -> dict | None:
    params = {"tokenIn": token_in, "tokenOut": token_out, "amountIn": str(amount_in)}
    headers = {"X-Client-Id": KYBER_CLIENT_ID}
    async with session.get(
        KYBER_ROUTES, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=20)
    ) as resp:
        payload = await resp.json()
        if resp.status != 200 or payload.get("code") != 0:
            logger.debug("Kyber route failed: %s", payload.get("message", resp.status))
            return None
        return payload.get("data")


async def build_swap(
    token_in: str,
    token_out: str,
    amount_in: int,
    recipient: str,
    *,
    slippage_bps: int = 50,
    session: aiohttp.ClientSession,
) -> BuiltSwap | None:
    route_data = await fetch_route(token_in, token_out, amount_in, session=session)
    if route_data is None:
        return None

    route_summary = route_data.get("routeSummary")
    router = route_data.get("routerAddress") or KYBER_ROUTER_BASE
    if not route_summary:
        return None

    body = {
        "routeSummary": route_summary,
        "sender": recipient,
        "recipient": recipient,
        "slippageTolerance": slippage_bps,
        "enableGasEstimation": False,
    }
    headers = {"X-Client-Id": KYBER_CLIENT_ID, "Content-Type": "application/json"}
    async with session.post(
        KYBER_BUILD, json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=25)
    ) as resp:
        payload = await resp.json()
        if resp.status != 200 or payload.get("code") != 0:
            logger.debug("Kyber build failed: %s", payload.get("message", resp.status))
            return None
        data = payload.get("data", {})
        calldata = data.get("data")
        if not calldata:
            return None
        amount_out = int(data.get("amountOut") or route_summary.get("amountOut") or 0)
        return BuiltSwap(
            token_in=token_in,
            token_out=token_out,
            amount_in=amount_in,
            amount_out=amount_out,
            router=router,
            calldata=calldata if calldata.startswith("0x") else f"0x{calldata}",
        )
