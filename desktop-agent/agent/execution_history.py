"""Persist liquidation execution records to disk."""

from __future__ import annotations

import json
import os
import fcntl
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent.models import LiquidationTarget

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_HISTORY_PATH = ROOT_DIR / "data" / "executions.json"


@dataclass
class ExecutionRecord:
    timestamp: str
    protocol_id: str
    protocol_name: str
    user: str
    health_factor: float
    estimated_profit_usd: float
    collateral_symbol: str
    debt_symbol: str
    user_op_hash: str | None
    status: str
    message: str
    tx_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_execution_result(cls, result: Any, tx_hash: str | None = None) -> ExecutionRecord:
        target: LiquidationTarget = result.target
        return cls(
            timestamp=datetime.now(UTC).isoformat(),
            protocol_id=target.protocol_id,
            protocol_name=target.protocol_name,
            user=target.user,
            health_factor=target.health_factor,
            estimated_profit_usd=target.estimated_profit_usd,
            collateral_symbol=target.collateral_symbol,
            debt_symbol=target.debt_symbol,
            user_op_hash=result.user_op_hash,
            status=result.status,
            message=result.message,
            tx_hash=tx_hash or getattr(result, "tx_hash", None),
        )


class ExecutionHistory:
    def __init__(self, path: Path | None = None, max_records: int = 500) -> None:
        self.path = path or DEFAULT_HISTORY_PATH
        self.max_records = max_records
        self._lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    def _acquire_lock(self) -> int:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._lock_path, os.O_CREAT | os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    @staticmethod
    def _release_lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    def load(self) -> list[dict[str, Any]]:
        fd = self._acquire_lock()
        try:
            if not self.path.exists():
                return []
            try:
                data = json.loads(self.path.read_text())
                return data if isinstance(data, list) else []
            except (json.JSONDecodeError, OSError):
                return []
        finally:
            self._release_lock(fd)

    def _write_records(self, records: list[dict[str, Any]]) -> None:
        payload = json.dumps(records, indent=2)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(payload)
        tmp_path.replace(self.path)

    def append(self, record: ExecutionRecord) -> dict[str, Any]:
        fd = self._acquire_lock()
        try:
            records = []
            if self.path.exists():
                try:
                    data = json.loads(self.path.read_text())
                    records = data if isinstance(data, list) else []
                except (json.JSONDecodeError, OSError):
                    records = []
            entry = record.to_dict()
            records.insert(0, entry)
            records = records[: self.max_records]
            self._write_records(records)
            return entry
        finally:
            self._release_lock(fd)

    @staticmethod
    def summary_from_records(records: list[dict[str, Any]]) -> dict[str, Any]:
        successful = [r for r in records if r.get("status") in ("complete", "success", "confirmed")]
        return {
            "total": len(records),
            "successful": len(successful),
            "last": records[0] if records else None,
        }

    def summary(self) -> dict[str, Any]:
        return self.summary_from_records(self.load())
