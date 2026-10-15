"""Human-readable reports (design §7): the refresh reports `update` writes next to
each catalog version -- a short Markdown summary for people (`render_refresh`) and a
full JSON detail for machines (`render_refresh_json`) -- and the mapping report the
`match` CLI and `OrgPrices.mapping_report` share (§9).

All three functions are pure string builders -- no disk I/O, no network -- so they are
exercised the same way whether called from `build.write_catalog`, a CLI, or a test.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import date as date_cls, datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from common.model_pricing.catalog import TOKEN_TYPES, MatchResult
from common.model_pricing.decimals import TOKENS_PER_UNIT

if TYPE_CHECKING:  # pragma: no cover - import cycle guard only
    from common.model_pricing.build import Added, BuildResult, Changed, Retired, SourceDiff
    from common.model_pricing.catalog import OrgPrices, UnmappedEntry


def format_rate_per_million(v: Decimal | None) -> str:
    """A stored USD-per-token rate as a USD-per-1M-tokens display string (§7: "rates
    (USD / 1M)"), `"-"` for a missing rate (never 0, §2.2). Trailing zeros produced by
    the multiply-back are trimmed for readability; this is a report, not the on-disk
    canonical form (`decimals.to_rate_str` owns that)."""
    if v is None:
        return "-"
    scaled = v * TOKENS_PER_UNIT
    text = format(scaled, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


# ============================================================================= refresh (§7)


def _iso_utc(dt: datetime) -> str:
    """`dt` as `YYYY-MM-DDTHH:MM:SSZ` -- the same style the catalog schema's own
    provenance timestamps use (§3.3, e.g. `"fetched_at": "2026-09-25T09:00:00Z"`).
    `dt` is documented as UTC (`BuildResult.run_at`); an aware value is converted to
    UTC first in case a caller ever hands in something else, a naive value is trusted
    as already UTC."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    """A GitHub-flavored Markdown table as report lines, or a single `"- (none)"` line
    when `rows` is empty -- the Summary's sources/channels tables (§7)."""
    if not rows:
        return ["- (none)"]
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return lines


def _source_status(prov: dict[str, Any]) -> str:
    """`"skipped"` for a cloud source `build()` never called (no channel of its cloud
    configured, or explicitly disabled, §6.2/§4.2 -- `build._skipped_entry`'s shape);
    `"ok"` otherwise. A source whose fetch failed never reaches a report at all
    (`build()` raises `SourceError` and nothing is produced, §6.1), so those two are
    the only statuses a written refresh report can show."""
    return "skipped" if prov.get("status") == "skipped" else "ok"


def _source_provenance_cell(prov: dict[str, Any]) -> str:
    """`prov`'s fields other than `status` (already its own table column) joined into
    one cell, e.g. `commit_date: ..., commit_sha: ...` for LiteLLM or `reason: no
    channels configured` for a skipped source."""
    fields = {k: v for k, v in prov.items() if k != "status"}
    if not fields:
        return "-"
    return ", ".join(f"{k}: {v}" for k, v in sorted(fields.items()))


def render_refresh(result: "BuildResult") -> str:
    """The refresh **summary** written alongside a catalog version
    (`<version>.refresh.md`, §7), for people: a header (version, build date, run time
    UTC, previous version or "first version", result), a `## Summary` of tables and
    counts only -- a sources table, a channels table, added/changed/retired counts (or
    "first version" on the first build), unmapped counts grouped by `(source, reason)`,
    and the source-diff count -- and a `## Errors` section listing excluded rows and
    warnings one line each, or "None".

    Deliberately has **no per-record lists** (§7): it never names an individual
    unmapped record, added/changed/retired model, or source-diff row --
    `render_refresh_json` carries that detail for anyone investigating a problem.
    """
    version = result.version
    lines: list[str] = [f"# Refresh report — {version.version}", ""]
    lines.append(f"- built_on: {version.built_on.isoformat()}")
    lines.append(f"- run time (UTC): {_iso_utc(result.run_at)}")
    lines.append(f"- previous version: {result.previous_version or 'first version'}")
    lines.append(f"- result: {'no-op' if result.is_noop else 'new version'}")
    lines.append("")

    lines.append("## Summary")
    lines.append("")

    lines.append("### Sources")
    won_counts = Counter(r.source for r in version.rates)
    unmapped_source_counts = Counter(u.source for u in result.unmapped)
    lines += _md_table(
        ["source", "status", "provenance", "rows won", "unmapped"],
        [
            [
                f"`{name}`",
                _source_status(result.source_provenance[name]),
                _source_provenance_cell(result.source_provenance[name]),
                str(won_counts.get(name, 0)),
                str(unmapped_source_counts.get(name, 0)),
            ]
            for name in sorted(result.source_provenance)
        ],
    )
    lines.append("")

    lines.append("### Channels")
    lines += _md_table(
        ["channel", "rows", "first source", "LiteLLM fallback"],
        [
            [
                f"`{channel}`",
                str(counts.get("first", 0) + counts.get("fallback", 0)),
                str(counts.get("first", 0)),
                str(counts.get("fallback", 0)),
            ]
            for channel, counts in sorted(result.channel_source_counts.items())
        ],
    )
    lines.append("")

    if result.is_first_build:
        lines.append("- first version (no previous catalog to diff against)")
    else:
        lines.append(f"- added: {len(result.added)}")
        lines.append(f"- changed: {len(result.changed)}")
        lines.append(f"- retired: {len(result.retired)}")
    lines.append("")

    unmapped_group_counts = Counter((u.source, u.reason) for u in result.unmapped)
    lines.append("- unmapped, by source and reason:")
    if unmapped_group_counts:
        for (source, reason), count in sorted(unmapped_group_counts.items()):
            lines.append(f"  - `{source}`: {reason} ({count})")
    else:
        lines.append("  - (none)")
    lines.append("")

    lines.append(f"- source diffs (winner != litellm): {len(result.source_diff)}")
    lines.append("")

    lines.append("## Errors")
    error_lines = [
        f"- excluded: `{e['channel']}`/`{e['model_id']}` ({e['source']}): {e['reason']}"
        for e in result.excluded
    ] + [f"- warning: {w}" for w in result.warnings]
    lines += error_lines if error_lines else ["- None"]
    lines.append("")

    return "\n".join(lines) + "\n"


def _added_dict(a: "Added") -> dict[str, Any]:
    return {"channel": a.channel, "model_id": a.model_id, "rates": a.rates}


def _changed_dict(c: "Changed") -> dict[str, Any]:
    return {
        "channel": c.channel,
        "model_id": c.model_id,
        "token_type": c.token_type,
        "old": c.old,
        "new": c.new,
        "pct": f"{c.pct:.2f}" if c.pct is not None else None,
    }


def _retired_dict(r: "Retired") -> dict[str, Any]:
    return {
        "channel": r.channel,
        "model_id": r.model_id,
        "retired_on": r.retired_on.isoformat(),
        "is_new": r.is_new,
        "last_rates": r.last_rates,
    }


def _source_diff_dict(d: "SourceDiff") -> dict[str, Any]:
    return {
        "channel": d.channel,
        "model_id": d.model_id,
        "source": d.source,
        "winner_rates": d.winner_rates,
        "litellm_rates": d.litellm_rates,
    }


def _unmapped_dict(u: "UnmappedEntry") -> dict[str, Any]:
    return {"source": u.source, "source_key": u.source_key, "reason": u.reason}


def render_refresh_json(result: "BuildResult") -> str:
    """The refresh **detail** written alongside a catalog version
    (`<version>.refresh.json`, §7), for machines: everything the Markdown summary
    leaves out -- the full `added`/`changed`/`retired`/`source_diff`/`unmapped` lists,
    plus run time, provenance, and channel counts, and the raw `excluded`/`warnings`
    lists. Used when investigating a problem the summary only counted.

    Stable key order, both at the top level and within each list entry (a plain
    `dict` literal per entry, inserted in the same order every call -- Python dicts
    preserve insertion order and `json.dumps` does not reorder unless `sort_keys` is
    passed, which it is not here). Every `Decimal` rate on `result`'s dataclasses is
    already a canonical string (`decimals.to_rate_str`, applied when `build()` built
    them); `Changed.pct` is a percentage, not a rate, so it is formatted separately
    here as a fixed 2-decimal string (`"10.00"`), `None` staying `null`. Dates are ISO
    strings.
    """
    version = result.version
    data: dict[str, Any] = {
        "version": version.version,
        "built_on": version.built_on.isoformat(),
        "run_at": _iso_utc(result.run_at),
        "previous_version": result.previous_version,
        "is_first_build": result.is_first_build,
        "is_noop": result.is_noop,
        "sources": result.source_provenance,
        "channel_source_counts": result.channel_source_counts,
        "added": [
            _added_dict(a) for a in sorted(result.added, key=lambda a: (a.channel, a.model_id))
        ],
        "changed": [
            _changed_dict(c)
            for c in sorted(result.changed, key=lambda c: (c.channel, c.model_id, c.token_type))
        ],
        "retired": [
            _retired_dict(r) for r in sorted(result.retired, key=lambda r: (r.channel, r.model_id))
        ],
        "source_diff": [
            _source_diff_dict(d)
            for d in sorted(result.source_diff, key=lambda d: (d.channel, d.model_id))
        ],
        "unmapped": [
            _unmapped_dict(u)
            for u in sorted(result.unmapped, key=lambda u: (u.source, u.source_key))
        ],
        "excluded": result.excluded,
        "warnings": result.warnings,
    }
    return json.dumps(data, indent=2)


# ============================================================================= mapping (§7, §9)


def _effective_row(prices: "OrgPrices", match: MatchResult, on_date: date_cls) -> Any | None:
    """The row `OrgPrices.rate` would read from for `match` on `on_date` (§3.4
    forward/backward), regardless of retirement -- used here only to show provenance
    (`source`/`source_key`); actual rate values still go through `prices.rate`, which
    does apply the retired rule, so a retired model's rates render as `-` even though
    its last known source is still shown."""
    if match.status != "matched" or match.channel is None or match.model_id is None:
        return None
    rows = prices.rows_by_key.get((match.channel, match.model_id))
    if not rows:
        return None
    candidates = [r for r in rows if r.start_date <= on_date]
    return max(candidates, key=lambda r: r.start_date) if candidates else rows[0]


def _mapping_entries(
    results: list[MatchResult], prices: "OrgPrices | None", on_date: date_cls | None
) -> list[dict[str, Any]]:
    """One dict per `MatchResult`, failures first (§7), with rates/source filled in
    only when `prices` and `on_date` are given."""
    ordered = sorted(results, key=lambda m: m.status == "matched")
    entries: list[dict[str, Any]] = []
    for m in ordered:
        row = _effective_row(prices, m, on_date) if prices is not None and on_date is not None else None
        rates: dict[str, str | None] = {tt: None for tt in TOKEN_TYPES}
        if prices is not None and on_date is not None and m.status == "matched":
            for tt in TOKEN_TYPES:
                rates[tt] = format_rate_per_million(prices.rate(m, tt, on_date))
        entries.append(
            {
                "usage_model_id": m.usage_model_id,
                "model_id": m.model_id,
                "step": m.step,
                "vendor": m.vendor,
                "channel": m.channel,
                "status": m.status,
                "source": row.source if row is not None else None,
                "source_key": row.source_key if row is not None else None,
                "rates": rates,
                "reason": m.reason,
            }
        )
    return entries


def render_mapping(
    results: list[MatchResult],
    fmt: str = "md",
    prices: "OrgPrices | None" = None,
    date: date_cls | None = None,
) -> str:
    """The mapping report (§7, §9): for each observed model string, `model_id` (and
    which §5.3 step matched it) → vendor → channel → row source/`source_key` → rates
    (USD per 1M) → reason. Failures (`unmatched`/`ambiguous`) first.

    `prices`/`date` are optional: without them (e.g. rendering a bare list of
    `MatchResult`, as `catalog.OrgPrices.mapping_report` callers sometimes just want
    the resolution step without rates), the source/source_key/rates columns are blank.
    The `match` CLI always passes both.
    """
    entries = _mapping_entries(results, prices, date)

    if fmt == "json":
        return json.dumps(entries, indent=2)
    if fmt != "md":
        raise ValueError(f"unknown render_mapping format: {fmt!r} (expected 'md' or 'json')")

    header = [
        "usage_model_id",
        "model_id",
        "vendor",
        "channel",
        "source",
        "source_key",
        *TOKEN_TYPES,
        "reason",
    ]
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join(["---"] * len(header)) + "|",
    ]
    for e in entries:
        model_id_cell = f"{e['model_id']} ({e['step']})" if e["model_id"] else "-"
        rate_cells = [e["rates"][tt] or "-" for tt in TOKEN_TYPES]
        row_cells = [
            e["usage_model_id"],
            model_id_cell,
            e["vendor"] or "-",
            e["channel"] or "-",
            e["source"] or "-",
            e["source_key"] or "-",
            *rate_cells,
            e["reason"] or "-",
        ]
        lines.append("| " + " | ".join(str(c) for c in row_cells) + " |")

    return "\n".join(lines) + "\n"
