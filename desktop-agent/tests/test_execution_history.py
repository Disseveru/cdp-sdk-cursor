"""Tests for execution history persistence."""

from __future__ import annotations

import json

from agent.execution_history import ExecutionHistory, ExecutionRecord
from agent.executor import ExecutionResult


def test_execution_record_roundtrip(aave_target, tmp_path):
    result = ExecutionResult(
        target=aave_target,
        user_op_hash="0xabc123",
        status="complete",
        message="done",
        tx_hash="0xdef456",
    )
    record = ExecutionRecord.from_execution_result(result)
    assert record.protocol_id == "aave-v3"
    assert record.user_op_hash == "0xabc123"
    assert record.tx_hash == "0xdef456"

    history = ExecutionHistory(path=tmp_path / "executions.json")
    history.append(record)
    loaded = history.load()
    assert len(loaded) == 1
    assert loaded[0]["user"] == aave_target.user
    assert history.summary()["total"] == 1


def test_execution_history_caps_records(aave_target, tmp_path):
    history = ExecutionHistory(path=tmp_path / "executions.json", max_records=3)
    for i in range(5):
        result = ExecutionResult(
            target=aave_target,
            user_op_hash=f"0x{i}",
            status="complete",
            message=f"run {i}",
        )
        history.append(ExecutionRecord.from_execution_result(result))
    assert len(history.load()) == 3
    assert history.load()[0]["user_op_hash"] == "0x4"
