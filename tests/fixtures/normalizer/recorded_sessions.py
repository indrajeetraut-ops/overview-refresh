"""Loaders for the committed, sanitized real-session fixtures under `recorded/`.

These are real Claude Code sessions (sanitized by `recorded/sanitize.py`), the "recorded CC
sessions" normalizer.md §12 asks for -- as opposed to the hand-built `SessionSource`s in
`sessions.py`. Both the real-data replay tests and the shape-contract test load through here so
they exercise the exact same on-disk layout the batch path delivers.
"""
from __future__ import annotations

from pathlib import Path

from collector.normalizer.local_loader import load_session_source
from collector.normalizer.models import SessionSource

RECORDED_DIR = Path(__file__).resolve().parent / "recorded"

# Ordered so anything derived from them (e.g. the shape golden) is deterministic.
RECORDED_NAMES = ["session-regular", "session-subagents"]


def load_recorded(name: str) -> SessionSource:
    return load_session_source(RECORDED_DIR / f"{name}.jsonl")


def all_recorded() -> list[SessionSource]:
    return [load_recorded(name) for name in RECORDED_NAMES]
