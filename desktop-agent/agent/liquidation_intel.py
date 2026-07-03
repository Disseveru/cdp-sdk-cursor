"""Optional paid liquidation intelligence via Agentic Market (x402) and free fallbacks."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

import aiohttp

from config.settings import AgentSettings

logger = logging.getLogger(__name__)

KLYMAX_LEVELS_URL = "https://liquidation-oracle.api.klymax402.com/api/levels"
N0BRAINS_MAP_URL = "https://api.n0brains.com/x402/liquidation-map/{coin}"


@dataclass(frozen=True)
class LiquidationIntel:
    source: str
    asset: str
    summary: str
    risk_levels: list[dict[str, Any]]
    raw: dict[str, Any] | None = None


def _awal_available() -> bool:
    return shutil.which("npx") is not None


async def _awal_signed_in() -> bool:
    if not _awal_available():
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            "npx",
            "awal",
            "status",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        text = stdout.decode()
        return "signed in" in text.lower() and "not authenticated" not in text.lower()
    except Exception:
        return False


async def _awal_x402_get(url: str) -> dict[str, Any] | None:
    """Pay for an x402 endpoint via Agentic Wallet CLI when signed in."""
    if not await _awal_signed_in():
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "npx",
            "awal",
            "x402",
            "pay",
            url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.debug("awal x402 pay failed: %s", stderr.decode()[:300])
            return None
        return json.loads(stdout.decode())
    except Exception as exc:
        logger.debug("awal x402 pay error: %s", exc)
        return None


async def fetch_klymax_levels(
    settings: AgentSettings,
    *,
    asset: str = "ETH",
    protocol: str = "moonwell",
    min_value_usd: float = 1000.0,
    limit: int = 10,
) -> LiquidationIntel | None:
    """Fetch DeFi liquidation risk levels (paid via Agentic Wallet when configured)."""
    query = (
        f"{KLYMAX_LEVELS_URL}?asset={asset}&protocol={protocol}"
        f"&minValueUsd={min_value_usd}&limit={limit}"
    )
    if settings.agentic_wallet_enabled:
        paid = await _awal_x402_get(query)
        if paid is not None:
            levels = paid if isinstance(paid, list) else paid.get("levels", paid.get("data", []))
            if isinstance(levels, dict):
                levels = levels.get("items", [])
            return LiquidationIntel(
                source="klymax-x402",
                asset=asset,
                summary=f"{len(levels)} risk levels from Klymax oracle",
                risk_levels=list(levels) if isinstance(levels, list) else [],
                raw=paid if isinstance(paid, dict) else None,
            )

    # Free fallback: no auth required for macro context only
    return LiquidationIntel(
        source="disabled",
        asset=asset,
        summary="Agentic Wallet not signed in — enable AGENTIC_WALLET_ENABLED after `npx awal auth login`",
        risk_levels=[],
    )


async def fetch_liquidation_map(
    settings: AgentSettings,
    *,
    coin: str = "ETH",
) -> LiquidationIntel | None:
    """Fetch liquidation heatmap clusters (paid via Agentic Wallet when configured)."""
    url = N0BRAINS_MAP_URL.format(coin=coin.upper())
    if settings.agentic_wallet_enabled:
        paid = await _awal_x402_get(url)
        if paid is not None:
            clusters = paid.get("clusters", paid.get("data", paid))
            if not isinstance(clusters, list):
                clusters = []
            return LiquidationIntel(
                source="n0brains-x402",
                asset=coin.upper(),
                summary=f"Liquidation map for {coin.upper()} ({len(clusters)} clusters)",
                risk_levels=clusters,
                raw=paid if isinstance(paid, dict) else None,
            )
    return None


async def enrich_volatility_context(settings: AgentSettings) -> dict[str, Any]:
    """Pull optional paid intel to widen watch-list priority during cascade risk."""
    if not settings.liquidation_intel_enabled:
        return {"enabled": False}

    klymax, liq_map = await asyncio.gather(
        fetch_klymax_levels(settings, asset="ETH", protocol="moonwell"),
        fetch_liquidation_map(settings, coin="ETH"),
    )
    context: dict[str, Any] = {"enabled": True, "sources": []}
    if klymax and klymax.risk_levels:
        context["sources"].append(klymax.source)
        context["klymax_levels"] = klymax.risk_levels[:10]
        context["klymax_summary"] = klymax.summary
    if liq_map and liq_map.risk_levels:
        context["sources"].append(liq_map.source)
        context["liquidation_map"] = liq_map.risk_levels[:5]
        context["map_summary"] = liq_map.summary
    if not context["sources"]:
        context["note"] = (
            "Sign in to Agentic Wallet (`npx awal auth login`) and fund USDC on Base "
            "to enable paid liquidation intel (~$0.003–0.005 per query)."
        )
    return context


async def search_agentic_services(query: str = "liquidation defi") -> list[dict[str, Any]]:
    """List Agentic Market services (free catalog lookup)."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.agentic.market/v1/services/search",
                params={"q": query},
                timeout=15,
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                return list(data.get("services", []))[:8]
    except Exception as exc:
        logger.debug("agentic market search failed: %s", exc)
        return []
