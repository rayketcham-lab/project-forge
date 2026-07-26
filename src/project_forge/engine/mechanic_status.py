"""Mechanic run progress (#100) — a tiny file-based status channel.

The mechanic runs as a DETACHED process, so the web server can't see its
in-memory state. It writes its current stage to a small JSON file after each
transition; the /mechanic page polls `/api/mechanic/status` and animates it,
because a run takes minutes (clone + agent + full-suite gate).
"""

from __future__ import annotations

import json
import logging
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_STATUS_FILE = Path(tempfile.gettempdir()) / "forge-mechanic-status.json"

# A non-terminal status older than this is treated as a dead run.
_STALE_AFTER = 3600.0

# stage -> human message ({item} is filled from the payload).
STAGE_MESSAGES = {
    "selecting": "Picking the highest-priority Think Tank item…",
    "cloning": "Cloning the repo into an isolated workspace…",
    "implementing": "Claude is implementing “{item}” — this is the long part (several minutes)…",
    "gating": "Running the full test suite + lint on the change…",
    "opening_pr": "All green — opening a pull request…",
    "pr_opened": "✓ PR opened — refresh to review it in the panel.",
    "gate_failed": "Gate failed — the change was incomplete or red, so no PR was opened.",
    "agent_failed": "The agent run failed — no PR.",
    "no_change": "The agent made no changes — no PR.",
    "no_work": "No Think Tank items to work right now.",
    "error": "The mechanic run errored — no PR.",
    "idle": "No mechanic run in progress.",
}

_TERMINAL = {"pr_opened", "gate_failed", "agent_failed", "no_change", "no_work", "error", "idle"}


def write_status(stage: str, *, item: str = "", detail: str = "") -> None:
    """Record the current stage. Best-effort — never raises into the run."""
    payload = {
        "stage": stage,
        "item": item,
        "detail": (detail or "")[:400],
        "terminal": stage in _TERMINAL,
        "updated_at": time.time(),
    }
    try:
        _STATUS_FILE.write_text(json.dumps(payload))
    except OSError:
        logger.debug("could not write mechanic status", exc_info=True)


def _idle() -> dict:
    return {"stage": "idle", "item": "", "detail": "", "terminal": True, "message": STAGE_MESSAGES["idle"]}


def read_status() -> dict:
    """Current run status for the panel, with a rendered `message`. Returns an
    idle payload when there is no run or the last one went stale."""
    try:
        data = json.loads(_STATUS_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return _idle()
    if not data.get("terminal") and time.time() - float(data.get("updated_at", 0) or 0) > _STALE_AFTER:
        return _idle()
    stage = data.get("stage", "idle")
    template = STAGE_MESSAGES.get(stage, stage)
    data["message"] = template.format(item=data.get("item") or "the item")
    return data
