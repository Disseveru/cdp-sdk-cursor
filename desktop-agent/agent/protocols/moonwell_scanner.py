"""Moonwell liquidation scanner (Compound V2 fork on Base)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from web3 import Web3
from web3.contract import Contract

from agent.dynamic_profit import effective_min_profit_usd
from agent.models import LiquidationTarget, WatchTarget
from agent.profit_engine import estimate_profit_with_swap_quote, resolve_swap_fee
from agent.protocols.base import ProtocolScanner
from agent.protocols.moonwell_base import (
    COMPTROLLER_ABI,
    ERC20_ABI,
    MOONWELL_BASE,
    MTOKEN_ABI,
    PRICE_ORACLE_ABI,
)
from config.settings import AgentSettings

logger = logging.getLogger(__name__)

MANTISSA = 10**18


class MoonwellScanner(ProtocolScanner):
    protocol_id = "moonwell"
    display_name = "Moonwell"
    executable = True

    def __init__(self, settings: AgentSettings, w3: Web3) -> None:
        super().__init__(settings, w3)
        if settings.network != "base":
            raise ValueError("Moonwell scanner only supports Base mainnet")
        self.comptroller: Contract = w3.eth.contract(
            address=Web3.to_checksum_address(MOONWELL_BASE["comptroller"]),
            abi=COMPTROLLER_ABI,
        )
        self._markets: list[dict[str, Any]] = []
        self._close_factor: float = 0.5
        self._liq_incentive_bps: int = 800
        self._oracle: Contract | None = None

    def _cache_path(self) -> Path:
        return self.settings.borrower_cache_dir / f"{self.protocol_id}.json"

    async def _load_markets(self) -> None:
        if self._markets:
            return
        try:
            self._close_factor = self.comptroller.functions.closeFactorMantissa().call() / MANTISSA
            incentive = self.comptroller.functions.liquidationIncentiveMantissa().call()
            self._liq_incentive_bps = int((incentive / MANTISSA - 1) * 10_000)
        except Exception:
            pass

        if self._oracle is None:
            try:
                oracle_addr = self.comptroller.functions.oracle().call()
                self._oracle = self.w3.eth.contract(
                    address=Web3.to_checksum_address(oracle_addr),
                    abi=PRICE_ORACLE_ABI,
                )
            except Exception:
                self._oracle = None

        market_addrs = list(MOONWELL_BASE["markets"].values())
        try:
            on_chain = self.comptroller.functions.getAllMarkets().call()
            market_addrs = list({Web3.to_checksum_address(a) for a in on_chain + market_addrs})
        except Exception:
            market_addrs = [Web3.to_checksum_address(a) for a in market_addrs]

        for mtoken_addr in market_addrs:
            mtoken = self.w3.eth.contract(address=mtoken_addr, abi=MTOKEN_ABI)
            try:
                underlying = mtoken.functions.underlying().call()
                symbol = mtoken.functions.symbol().call()
                erc20 = self.w3.eth.contract(
                    address=Web3.to_checksum_address(underlying), abi=ERC20_ABI
                )
                decimals = erc20.functions.decimals().call()
                underlying_symbol = erc20.functions.symbol().call()
            except Exception:
                continue
            self._markets.append(
                {
                    "mtoken": mtoken_addr,
                    "underlying": Web3.to_checksum_address(underlying),
                    "symbol": underlying_symbol,
                    "mtoken_symbol": symbol,
                    "decimals": int(decimals),
                    "contract": mtoken,
                }
            )

    async def discover_borrowers(self, limit: int = 500) -> list[str]:
        await self._load_markets()
        borrowers: set[str] = set()
        cache_path = self._cache_path()
        if cache_path.exists():
            cached = json.loads(cache_path.read_text())
            borrowers.update(cached.get("borrowers", []))

        # Incremental: scan recent blocks; full backfill only when cache is empty
        blocks = 25_000 if len(borrowers) < 10 else 3_000
        event_users = await self._scan_borrow_events(blocks=blocks)
        borrowers.update(event_users)

        ordered = sorted(borrowers)[:limit]
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {"updated_at": datetime.now(UTC).isoformat(), "borrowers": ordered},
                indent=2,
            )
        )
        return ordered

    async def _scan_borrow_events(self, blocks: int = 50_000) -> list[str]:
        latest = self.w3.eth.block_number
        from_block = max(0, latest - blocks)
        borrow_topic = "0x" + self.w3.keccak(
            text="Borrow(address,uint256,uint256,uint256)"
        ).hex()

        users: set[str] = set()
        chunk = 999
        priority = {
            Web3.to_checksum_address(MOONWELL_BASE["markets"]["mUSDC"]),
            Web3.to_checksum_address(MOONWELL_BASE["markets"]["mWETH"]),
            Web3.to_checksum_address(MOONWELL_BASE["markets"]["mcbETH"]),
        }
        scan_markets = [m for m in self._markets if m["mtoken"] in priority] or self._markets[:3]
        for market in scan_markets:
            start = from_block
            while start <= latest:
                end = min(start + chunk - 1, latest)
                try:
                    logs = self.w3.eth.get_logs(
                        {
                            "address": market["mtoken"],
                            "fromBlock": start,
                            "toBlock": end,
                            "topics": [borrow_topic],
                        }
                    )
                    for log in logs:
                        borrower: str | None = None
                        if len(log.get("topics", [])) >= 2:
                            borrower = Web3.to_checksum_address(
                                "0x" + log["topics"][1].hex()[-40:]
                            )
                        elif log.get("data") and len(log["data"]) >= 66:
                            borrower = Web3.to_checksum_address(
                                "0x" + log["data"].hex()[26:66]
                            )
                        if borrower:
                            users.add(borrower)
                except Exception as exc:
                    logger.warning("Moonwell borrow chunk %s-%s failed: %s", start, end, exc)
                start = end + 1
        return list(users)

    async def evaluate_user(self, user: str, *, macro_active: bool = False) -> LiquidationTarget | None:
        return await self._evaluate_user(user, macro_active=macro_active)

    async def fetch_watch_positions(self, limit: int = 50) -> list[WatchTarget]:
        """Borrowers with thin liquidity buffer (near liquidation, not yet underwater)."""
        await self._load_markets()
        borrowers = await self.discover_borrowers(limit=limit)
        watches: list[WatchTarget] = []
        for user in borrowers:
            try:
                _err, liquidity, shortfall = self.comptroller.functions.getAccountLiquidity(user).call()
            except Exception:
                continue
            if shortfall > 0:
                continue
            liquidity_usd = liquidity / MANTISSA
            if liquidity_usd <= 0:
                continue
            # Approximate HF from liquidity buffer vs borrow value (both in USD).
            try:
                total_borrow_usd = 0.0
                for market in self._markets:
                    borrow = market["contract"].functions.borrowBalanceStored(user).call()
                    if borrow <= 0:
                        continue
                    if self._oracle is not None:
                        price = self._oracle.functions.getUnderlyingPrice(market["mtoken"]).call()
                        total_borrow_usd += borrow * price / MANTISSA
                    else:
                        total_borrow_usd += borrow / (10 ** market["decimals"])
                if total_borrow_usd <= 0:
                    continue
                hf_proxy = 1.0 + (liquidity_usd / max(total_borrow_usd, 1e-9))
            except Exception:
                hf_proxy = 1.02
            if hf_proxy >= self.settings.watch_hf_threshold or hf_proxy < 1.0:
                continue
            pair = await self._best_liquidation_pair(user)
            if pair is None:
                continue
            borrow_market, collateral_market, _debt_raw = pair
            watches.append(
                WatchTarget(
                    protocol_id=self.protocol_id,
                    protocol_name=self.display_name,
                    user=user,
                    health_factor=hf_proxy,
                    collateral_symbol=collateral_market["symbol"],
                    debt_symbol=borrow_market["symbol"],
                    debt_usd=total_borrow_usd,
                    collateral_usd=liquidity_usd + total_borrow_usd,
                    price_to_liquidation_pct=max(0.0, (hf_proxy - 1.0) * 100),
                    market_id=borrow_market["mtoken"],
                )
            )
        watches.sort(key=lambda w: w.health_factor)
        return watches[:limit]

    async def scan(self, borrowers: list[str] | None = None) -> list[LiquidationTarget]:
        await self._load_markets()
        if borrowers is None:
            borrowers = await self.discover_borrowers()

        targets: list[LiquidationTarget] = []
        for user in borrowers:
            target = await self._evaluate_user(user)
            if target is not None:
                targets.append(target)

        targets.sort(key=lambda t: t.estimated_profit_usd, reverse=True)
        return targets

    async def _evaluate_user(self, user: str, *, macro_active: bool = False) -> LiquidationTarget | None:
        try:
            _err, liquidity, shortfall = self.comptroller.functions.getAccountLiquidity(
                user
            ).call()
        except Exception:
            return None

        if shortfall == 0:
            return None

        liquidity_usd = liquidity / MANTISSA
        shortfall_usd = shortfall / MANTISSA
        total_debt_usd = liquidity_usd + shortfall_usd
        health_factor = liquidity_usd / total_debt_usd if total_debt_usd > 0 else 0.0

        if health_factor >= self.settings.health_factor_threshold:
            return None

        pair = await self._best_liquidation_pair(user)
        if pair is None:
            return None

        borrow_market, collateral_market, debt_raw = pair
        debt_decimals = borrow_market["decimals"]
        debt_to_cover = int(debt_raw * self._close_factor)
        if debt_to_cover == 0:
            return None

        bonus_bps = self._liq_incentive_bps

        est_collateral = int(
            self.comptroller.functions.liquidateCalculateSeizeTokens(
                borrow_market["mtoken"],
                collateral_market["mtoken"],
                debt_to_cover,
            ).call()[1]
        )

        estimated_profit, _ = await estimate_profit_with_swap_quote(
            self.settings,
            collateral_asset=collateral_market["underlying"],
            debt_asset=borrow_market["underlying"],
            collateral_amount=max(est_collateral, 1),
            debt_to_cover_human=debt_to_cover / (10**debt_decimals),
            liquidation_bonus_bps=bonus_bps,
            debt_decimals=debt_decimals,
            flash_fee_bps=5,
        )

        min_profit = effective_min_profit_usd(self.settings, health_factor, macro_active=macro_active)
        if estimated_profit < min_profit:
            return None

        swap_fee = resolve_swap_fee(collateral_market["symbol"], borrow_market["symbol"])
        moonwell_contract = bool(self.settings.moonwell_flash_liquidator_address)

        return LiquidationTarget(
            protocol_id=self.protocol_id,
            protocol_name=self.display_name,
            user=user,
            health_factor=health_factor,
            total_collateral_usd=liquidity_usd + shortfall_usd,
            total_debt_usd=total_debt_usd,
            collateral_asset=collateral_market["underlying"],
            collateral_symbol=collateral_market["symbol"],
            debt_asset=borrow_market["underlying"],
            debt_symbol=borrow_market["symbol"],
            debt_to_cover=debt_to_cover,
            debt_to_cover_human=debt_to_cover / (10**debt_decimals),
            estimated_profit_usd=estimated_profit,
            swap_fee=swap_fee,
            flash_amount=debt_to_cover,
            liquidation_bonus_bps=bonus_bps,
            executable=moonwell_contract,
            debt_decimals=debt_decimals,
            collateral_decimals=collateral_market["decimals"],
            mtoken_borrowed=borrow_market["mtoken"],
            mtoken_collateral=collateral_market["mtoken"],
            estimated_collateral_amount=est_collateral,
        )

    async def _best_liquidation_pair(
        self, user: str
    ) -> tuple[dict[str, Any], dict[str, Any], int] | None:
        best_borrow: dict[str, Any] | None = None
        best_debt = 0
        best_collateral: dict[str, Any] | None = None
        best_collateral_balance = 0

        for market in self._markets:
            mtoken = market["contract"]
            try:
                borrow = mtoken.functions.borrowBalanceStored(user).call()
                mtoken_balance = mtoken.functions.balanceOf(user).call()
                exchange_rate = mtoken.functions.exchangeRateStored().call()
                underlying_collateral = (mtoken_balance * exchange_rate) // MANTISSA
                is_member = self.comptroller.functions.checkMembership(
                    user, market["mtoken"]
                ).call()
            except Exception:
                continue

            if borrow > best_debt:
                best_debt = borrow
                best_borrow = market

            if is_member and underlying_collateral > best_collateral_balance:
                best_collateral_balance = underlying_collateral
                best_collateral = market

        if best_borrow is None or best_collateral is None or best_debt == 0:
            return None
        return (best_borrow, best_collateral, best_debt)
