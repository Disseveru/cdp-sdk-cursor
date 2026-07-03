"""Base Flashblocks / fast-head WebSocket subscriber for sub-second reaction."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

from agent.oracle_monitor import BASE_FEEDS
from config.settings import AgentSettings

logger = logging.getLogger(__name__)

DEFAULT_FLASHBLOCKS_WS = "wss://mainnet-preconf.base.org"
ANSWER_UPDATED_TOPIC = "0x0559884fd3a460db3073b7fc896cc77986f16e3782ded2a792836708fc36bffa"


OnOracleUpdate = Callable[[str, dict[str, Any]], Awaitable[None]]
OnNewHead = Callable[[int], Awaitable[None]]


class FlashblocksSubscriber:
    """Subscribe to preconfirmation newHeads and Chainlink AnswerUpdated logs."""

    def __init__(self, settings: AgentSettings) -> None:
        self.settings = settings
        self._ws_url = settings.flashblocks_ws_url or DEFAULT_FLASHBLOCKS_WS
        self._running = False
        self._task: asyncio.Task | None = None
        self._on_oracle: OnOracleUpdate | None = None
        self._on_head: OnNewHead | None = None
        self._last_head = 0

    def set_handlers(
        self,
        *,
        on_oracle_update: OnOracleUpdate | None = None,
        on_new_head: OnNewHead | None = None,
    ) -> None:
        self._on_oracle = on_oracle_update
        self._on_head = on_new_head

    async def start(self) -> None:
        if not self.settings.flashblocks_enabled:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run_loop(self) -> None:
        backoff = 1.0
        while self._running:
            try:
                await self._connect_and_listen()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Flashblocks WS disconnected: %s — retry in %.0fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _connect_and_listen(self) -> None:
        feed_addrs = [addr.lower() for addr in BASE_FEEDS.values()]
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                self._ws_url,
                heartbeat=20,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as ws:
                await ws.send_json(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "eth_subscribe",
                        "params": ["newHeads"],
                    }
                )
                await ws.send_json(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "eth_subscribe",
                        "params": [
                            "logs",
                            {
                                "address": feed_addrs,
                                "topics": [ANSWER_UPDATED_TOPIC],
                            },
                        ],
                    }
                )
                logger.info("Flashblocks WS connected: %s", self._ws_url)
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    payload = json.loads(msg.data)
                    await self._handle_message(payload, feed_addrs)

    async def _handle_message(self, payload: dict[str, Any], feed_addrs: list[str]) -> None:
        params = payload.get("params") or {}
        result = params.get("result") or {}
        if not isinstance(result, dict):
            return

        if "number" in result:
            block = int(result["number"], 16) if isinstance(result["number"], str) else int(result["number"])
            if block > self._last_head:
                self._last_head = block
                if self._on_head is not None:
                    await self._on_head(block)
            return

        address = (result.get("address") or "").lower()
        topics = result.get("topics") or []
        if topics and topics[0].lower() == ANSWER_UPDATED_TOPIC.lower():
            if address not in feed_addrs:
                return
            feed_name = next(
                (name for name, addr in BASE_FEEDS.items() if addr.lower() == address),
                None,
            )
            if feed_name is None or self._on_oracle is None:
                return
            await self._on_oracle(feed_name, result)
