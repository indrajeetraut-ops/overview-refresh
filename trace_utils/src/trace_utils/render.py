"""Rendering — turn library outputs into human-readable terminal output (rich)."""
from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.table import Table

from .profiler import Profile
from .sessions import SessionSummary

console = Console()


def render_datasets(rows: list[dict[str, Any]]) -> None:
    t = Table(title="Configured datasets", show_lines=False)
    for col in ("name", "repo_id", "fmt", "files", "size"):
        t.add_column(col, overflow="fold")
    for r in rows:
        t.add_row(
            r.get("name", ""), r.get("repo_id", ""), r.get("fmt", "?"),
            str(r.get("files", "?")), r.get("size", "?"),
        )
    console.print(t)


def render_shape(profile: Profile, title: str) -> None:
    header = f"{title}  ·  {profile.total_rows} rows sampled  ·  {len(profile.fields)} field paths"
    t = Table(title=header, show_lines=False)
    t.add_column("FIELD PATH", overflow="fold", style="cyan")
    t.add_column("TYPE")
    t.add_column("COVERAGE", justify="right")
    t.add_column("SAMPLE / TOP VALUES", overflow="fold")
    total = profile.total_rows
    for f in profile.fields:
        t.add_row(
            f.path, f.type_repr(), f"{f.coverage(total) * 100:.0f}%", f.sample_repr()
        )
    console.print(t)


def render_sessions(sessions: list[SessionSummary], title: str) -> None:
    t = Table(title=f"{title}  ·  {len(sessions)} sessions", show_lines=False)
    t.add_column("SESSION", overflow="fold", style="cyan")
    t.add_column("EVENTS", justify="right")
    t.add_column("FIRST", overflow="fold")
    t.add_column("LAST", overflow="fold")
    t.add_column("MODELS", overflow="fold")
    for s in sessions:
        t.add_row(
            s.session_id, str(s.n_events),
            (s.first_ts or "")[:19], (s.last_ts or "")[:19], s.models or "",
        )
    console.print(t)


def _extract_text(row: dict[str, Any]) -> tuple[str, str]:
    """Best-effort (role/type, text) from a raw event row."""
    msg = row.get("message")
    if isinstance(msg, dict):
        role = str(msg.get("role") or row.get("type") or "?")
        content = msg.get("content")
        return role, _stringify(content)
    if "assistant_response" in row or "user_prompt" in row:
        parts = []
        if row.get("user_prompt"):
            parts.append(f"[user] {row['user_prompt']}")
        if row.get("assistant_response"):
            parts.append(f"[assistant] {row['assistant_response']}")
        return str(row.get("model") or "exchange"), "\n".join(parts)
    return str(row.get("type") or "?"), _stringify(row)


def _stringify(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    try:
        return json.dumps(v, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(v)


def render_transcript(
    cols: list[str], rows: list[tuple], session_id: str, raw: bool = False
) -> None:
    if raw:
        for r in rows:
            console.print_json(json.dumps(dict(zip(cols, r)), default=str))
        return
    console.rule(f"Session {session_id}  ·  {len(rows)} events")
    for r in rows:
        row = dict(zip(cols, r))
        role, text = _extract_text(row)
        ts = str(row.get("timestamp") or "")[:19]
        style = {"user": "green", "assistant": "yellow"}.get(role, "white")
        console.print(f"[bold {style}]{role}[/] [dim]{ts}[/]")
        if text:
            console.print(text if len(text) < 4000 else text[:4000] + " …[truncated]")
        console.print()
