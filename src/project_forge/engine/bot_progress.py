"""Live narration for a money-bot generation cycle.

A cycle takes five to twelve minutes: a venue sweep, a generation, four
adversarial lenses in sequence, often a rewrite and a re-check. The
operator watched a spinner for all of it with no way to tell a working
engine from a hung one — the only honest answer was in a `ps` listing
showing which `claude --print` was in flight. The engine knows exactly what
it is doing at every step; it just never said so.

Deliberately an in-memory ring buffer rather than a table. This is
ephemeral telemetry about a run in flight, worthless five minutes after it
ends, and the durable record of what a cycle DID already exists in
`bot_probes`. A schema migration to hold log lines would be the wrong
trade.

Consequences of that choice, both acceptable: the tail is empty after a
reload, and a second concurrent run would interleave with the first. The
cadence works one program at a time, so the second case does not arise in
practice.
"""

from __future__ import annotations

import time
from collections import deque
from datetime import UTC, datetime
from typing import Any

# Enough to hold a full cycle's narration several times over. A cycle emits
# roughly a dozen events.
MAX_EVENTS = 60

# Long enough to carry an objection's first sentence, short enough that the
# tail stays scannable.
MAX_DETAIL = 500

_events: deque[dict[str, str]] = deque(maxlen=MAX_EVENTS)
_running: bool = False
_started_at: float | None = None
_outcome: str = ""


def reset() -> None:
    """Drop everything. Tests, and the start of a new run."""
    global _running, _started_at, _outcome
    _events.clear()
    _running = False
    _started_at = None
    _outcome = ""


def start_run() -> None:
    """Begin narrating a cycle, discarding the previous one's tail."""
    global _running, _started_at, _outcome
    reset()
    _running = True
    _started_at = time.monotonic()


def finish_run(outcome: str) -> None:
    """Mark the cycle done. The outcome survives so the tail still explains
    itself after the spinner stops."""
    global _running, _outcome
    _running = False
    _outcome = (outcome or "")[:MAX_DETAIL]


def emit(stage: str, detail: str = "") -> None:
    """Record one step. Never raises — narration must not break a run."""
    _events.append(
        {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "stage": str(stage)[:40],
            "detail": str(detail or "")[:MAX_DETAIL],
        }
    )


def recent() -> list[dict[str, str]]:
    """The tail, oldest first."""
    return list(_events)


def status() -> dict[str, Any]:
    """Whether a cycle is in flight, how long it has been going, and how the
    last one ended."""
    elapsed = 0.0
    if _started_at is not None:
        elapsed = round(time.monotonic() - _started_at, 1)
    return {
        "running": _running,
        "elapsed_seconds": elapsed,
        "outcome": _outcome,
        "events": recent(),
    }
