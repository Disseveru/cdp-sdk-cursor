"""Tests for watch-list calldata staging."""

from __future__ import annotations

from agent.watch_staging import WatchStager
from tests.conftest import aave_target, mock_wallet, settings


def test_watch_stager_caches_fresh_calldata(settings, mock_wallet, aave_target):
    from agent.executor import FlashLiquidationExecutor

    executor = FlashLiquidationExecutor(settings, mock_wallet)
    stager = WatchStager(settings, executor)
    staged = stager.stage_target(aave_target)
    assert staged is not None
    assert stager.staged_count == 1
    assert stager.get("aave-v3", aave_target.user) is not None
