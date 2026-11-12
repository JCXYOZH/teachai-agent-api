"""In-process counters for provider latency and generation job outcomes.

Counters are process-local and reset on restart. They exist to feed the
periodic ``metrics_snapshot`` log line, not to replace a metrics backend.
"""

import logging
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_SNAPSHOT_SECONDS = 300

_lock = threading.Lock()
_buckets: Dict[str, Dict[str, "_Bucket"]] = {}
_last_snapshot_at = time.monotonic()
_snapshot_seconds = DEFAULT_SNAPSHOT_SECONDS


@dataclass
class _Bucket:
    calls: int = 0
    failures: int = 0
    total_ms: float = 0.0
    max_ms: float = 0.0


def configure(snapshot_seconds: int) -> None:
    global _snapshot_seconds, _last_snapshot_at
    with _lock:
        _snapshot_seconds = max(0, snapshot_seconds)
        _last_snapshot_at = time.monotonic()


def reset() -> None:
    global _last_snapshot_at
    with _lock:
        _buckets.clear()
        _last_snapshot_at = time.monotonic()


def record(group: str, key: str, *, failed: bool, duration_ms: float) -> None:
    with _lock:
        bucket = _buckets.setdefault(group, {}).setdefault(key, _Bucket())
        bucket.calls += 1
        if failed:
            bucket.failures += 1
        bucket.total_ms += duration_ms
        bucket.max_ms = max(bucket.max_ms, duration_ms)
    maybe_log_snapshot()


def record_llm_call(
    provider_id: Optional[str],
    model: Optional[str],
    outcome: str,
    duration_ms: float,
) -> None:
    logger.info(
        "llm_call_completed provider=%s model=%s outcome=%s duration_ms=%.1f",
        provider_id or "unknown",
        model or "unknown",
        outcome,
        duration_ms,
    )
    record(
        "llm",
        provider_id or "unknown",
        failed=outcome != "ok",
        duration_ms=duration_ms,
    )


def record_generation_job(kind: str, outcome: str, duration_ms: float) -> None:
    record("generation_job", kind, failed=outcome != "ok", duration_ms=duration_ms)


def snapshot() -> List[str]:
    with _lock:
        return _snapshot_lines()


def maybe_log_snapshot(force: bool = False) -> None:
    global _last_snapshot_at
    with _lock:
        interval = _snapshot_seconds
        if not force:
            if not interval or (time.monotonic() - _last_snapshot_at) < interval:
                return
        _last_snapshot_at = time.monotonic()
        lines = _snapshot_lines()
    for snapshot_line in lines:
        logger.info("%s", snapshot_line)


def _snapshot_lines() -> List[str]:
    lines: List[str] = []
    for group in sorted(_buckets):
        for key in sorted(_buckets[group]):
            bucket = _buckets[group][key]
            failure_rate = bucket.failures / bucket.calls if bucket.calls else 0.0
            average_ms = bucket.total_ms / bucket.calls if bucket.calls else 0.0
            lines.append(
                "metrics_snapshot group=%s key=%s calls=%d failures=%d "
                "failure_rate=%.3f avg_ms=%.1f max_ms=%.1f"
                % (
                    group,
                    key,
                    bucket.calls,
                    bucket.failures,
                    failure_rate,
                    average_ms,
                    bucket.max_ms,
                )
            )
    return lines
