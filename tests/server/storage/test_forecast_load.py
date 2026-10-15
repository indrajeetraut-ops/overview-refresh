"""T5: `forecast_load` -- parsing and validation of `forecast_export.json` (no database), and
the one-transaction replace of `forecast_runs` / `forecast_scope_series` (DB-backed tests).

DB tests skip unless `TALIUM_TEST_DSN` is set; the ones that go through
`load_forecast_export` also need its database name to end in `_synth` (the loader's own guard),
the same posture as test_synthetic_load.py. Assertions read the database through their own
engine, never the shared process engine the loader disposes."""
import copy
import dataclasses
import hashlib
import json
import os
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from server.storage import forecast_load
from server.storage.config import StorageConfig
from server.storage.dbm import operations
from server.storage.engine import dispose_engine
from server.storage.forecast_load import (
    ForecastLoadError, ParsedExport, check_against_dataset, load_forecast_export, parse_export,
    replace_run,
)
from server.storage.queries import QueryCatalog

from .conftest import DSN, requires_db
from .forecast_export_builder import (
    GRID, dump_forecast_tables, make_export, make_series_entry, source_payload, write_export,
)

_synth_ready = DSN is not None and (make_url(DSN).database or "").endswith("_synth")
requires_synth_db = pytest.mark.skipif(
    not _synth_ready, reason="set TALIUM_TEST_DSN to a *_synth database to run this test")


@pytest.fixture
def export_path(tmp_path):
    return write_export(tmp_path / "forecast_export.json", make_export())


def _bytes_of(doc) -> bytes:
    return (json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


# =============================================================================== pure parsing

def test_valid_document_gives_the_run_id_rows_and_meta(tmp_path):
    doc = make_export()
    path = write_export(tmp_path / "e.json", doc)
    parsed = parse_export(path)

    assert parsed.run_id == hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    assert len(parsed.run_id) == 16
    assert parsed.origin == date(2026, 9, 30)
    assert parsed.data_through == date(2026, 10, 30)
    assert parsed.generated_at == datetime(2026, 10, 31, 10, 0, 0, tzinfo=timezone.utc)
    assert parsed.scope_count == 3 and parsed.preset_count == 2
    assert len(parsed.series) == 6

    # one row per scope x preset, scopes in export order, presets in preset order
    assert [(r.scope_type, r.scope_id, r.preset_id) for r in parsed.series] == [
        (t, i, p) for t, i in [("org", "default"), ("team", "development"),
                               ("repo", "github:acme/auth-service")]
        for p in ("baseline", "usage-up-20")]
    for row in parsed.series:
        assert row.status == "complete"
        assert row.payload == source_payload(doc, row.scope_type, row.scope_id, row.preset_id)
        assert set(row.payload) == {"daily", "periods"}

    assert parsed.meta == {
        "schema": "dashboard-forecast/1.0",
        "timezone": "Asia/Kolkata",
        "band": {"low": 10, "high": 90},
        "quantile_grid": GRID,
        "dates": doc["dates"],
        "periods": doc["periods"],
        "presets": doc["presets"],
        "scopes": [{"scope_type": "org", "scope_id": "default", "name": "acme.dev"},
                   {"scope_type": "team", "scope_id": "development", "name": "Development"},
                   {"scope_type": "repo", "scope_id": "github:acme/auth-service",
                    "name": "acme/auth-service"}],
    }


def test_run_id_is_stable_and_changes_with_the_bytes(tmp_path):
    a = write_export(tmp_path / "a.json", make_export())
    a_copy = write_export(tmp_path / "a_copy.json", make_export())
    only_generated_at_differs = write_export(
        tmp_path / "b.json", make_export(generated_at="2026-10-31T10:00:01Z"))
    assert parse_export(a).run_id == parse_export(a).run_id == parse_export(a_copy).run_id
    assert parse_export(only_generated_at_differs).run_id != parse_export(a).run_id
    # the same content with different whitespace is different bytes, so a different run
    pretty = tmp_path / "pretty.json"
    pretty.write_text(json.dumps(make_export(), sort_keys=True, indent=2), encoding="utf-8")
    assert parse_export(pretty).run_id != parse_export(a).run_id


def test_incomplete_entries_have_an_empty_payload_and_other_statuses_keep_numbers(tmp_path):
    doc = make_export()
    series = doc["scopes"][1]["series"]
    series["baseline"] = {"status": "incomplete"}
    series["usage-up-20"] = make_series_entry(99, "conditional")
    doc["scopes"][2]["series"]["baseline"] = make_series_entry(98, "inactive")
    parsed = parse_export(write_export(tmp_path / "e.json", doc))
    by_key = {(r.scope_id, r.preset_id): r for r in parsed.series}
    incomplete = by_key[("development", "baseline")]
    assert incomplete.status == "incomplete" and incomplete.payload == {}
    conditional = by_key[("development", "usage-up-20")]
    assert conditional.status == "conditional"
    assert conditional.payload == source_payload(doc, "team", "development", "usage-up-20")
    inactive = by_key[("github:acme/auth-service", "baseline")]
    assert inactive.status == "inactive" and "daily" in inactive.payload


def test_a_higher_minor_with_extra_keys_is_accepted_and_the_keys_are_kept(tmp_path):
    doc = make_export()
    doc["schema"] = "dashboard-forecast/1.7"
    doc["future_block"] = {"a": [1, 2], "b": None}
    doc["scopes"][0]["series"]["baseline"]["confidence_note"] = {"level": 3}
    doc["scopes"][0]["extra_scope_key"] = "ignored"
    parsed = parse_export(write_export(tmp_path / "e.json", doc))
    assert parsed.meta["schema"] == "dashboard-forecast/1.7"
    assert parsed.meta["future_block"] == {"a": [1, 2], "b": None}
    first = parsed.series[0]
    assert (first.scope_id, first.preset_id) == ("default", "baseline")
    assert first.payload["confidence_note"] == {"level": 3}      # extra series key survives
    assert set(first.payload) == {"daily", "periods", "confidence_note"}
    assert "status" not in first.payload
    # the known top-level keys are not duplicated into meta
    assert "generated_at" not in parsed.meta and "origin" not in parsed.meta
    assert "extra_scope_key" not in json.dumps(parsed.meta)    # scope-level extras are not kept


def test_scope_names_and_order_are_preserved_in_meta(tmp_path):
    specs = [("repo", "github:o/z", "o/z"), ("org", "acme", "Acme"), ("team", "qa", "QA")]
    parsed = parse_export(write_export(tmp_path / "e.json", make_export(specs)))
    assert parsed.meta["scopes"] == [
        {"scope_type": "repo", "scope_id": "github:o/z", "name": "o/z"},
        {"scope_type": "org", "scope_id": "acme", "name": "Acme"},
        {"scope_type": "team", "scope_id": "qa", "name": "QA"}]


def test_zero_org_scopes_are_accepted(tmp_path):
    specs = [("team", "qa", "QA"), ("repo", "github:o/z", "o/z")]
    assert parse_export(write_export(tmp_path / "e.json", make_export(specs))).scope_count == 2


# ------------------------------------------------------------------------------ refusals

def test_a_missing_file_is_refused(tmp_path):
    with pytest.raises(ForecastLoadError, match="cannot read forecast export"):
        parse_export(tmp_path / "nope.json")


def test_a_directory_is_refused(tmp_path):
    with pytest.raises(ForecastLoadError, match="cannot read forecast export"):
        parse_export(tmp_path)


@pytest.mark.parametrize("content, match", [
    (b"", "not valid JSON"),
    (b"{", "not valid JSON"),
    (b'{"schema": "dashboard-forecast/1.0"', "not valid JSON"),
    (b"\xff\xfe\x00", "not valid JSON"),
    (b'{"a": NaN}', "not valid JSON"),
    (b'{"a": Infinity}', "not valid JSON"),
    (b'{"a": -Infinity}', "not valid JSON"),
    (b'{"a": 1e999}', "outside the representable range"),
    (b'{"name": "a\\u0000b"}', "NUL character"),
    (b'{"a\\u0000": 1}', "NUL character"),
    (b'{"name": "\\ud800"}', "unpaired surrogate"),
    (b'{"a": [{"b": "x\\u0000"}]}', "NUL character"),
    (b'{"a": [1, [2, 1e999]]}', "outside the representable range"),
    (b'{"a": ["ok", ["\\ud800"]]}', "unpaired surrogate"),
    (b"[" * 200_000 + b"]" * 200_000, "nested too deeply"),
    (b"[]", "JSON object"),
    (b'"dashboard-forecast/1.0"', "JSON object"),
    (b"null", "JSON object"),
    (b"42", "JSON object"),
])
def test_files_that_are_not_a_json_object_are_refused(tmp_path, content, match):
    path = tmp_path / "e.json"
    path.write_bytes(content)
    with pytest.raises(ForecastLoadError, match=match):
        parse_export(path)


def test_a_truncated_real_document_is_refused(tmp_path):
    raw = _bytes_of(make_export())
    path = tmp_path / "e.json"
    path.write_bytes(raw[: len(raw) // 2])
    with pytest.raises(ForecastLoadError, match="not valid JSON"):
        parse_export(path)


def _set(path, value):
    """A mutator that sets `doc[path[0]][path[1]]... = value` (list indexes as ints)."""
    def mutate(doc):
        node = doc
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
    return mutate


def _delete(path):
    def mutate(doc):
        node = doc
        for key in path[:-1]:
            node = node[key]
        del node[path[-1]]
    return mutate


_S0 = ["scopes", 0, "series", "baseline"]      # org scope, baseline entry
_P0 = _S0 + ["periods", 0]                     # its first period (1 forecast day)
_P1 = _S0 + ["periods", 1]                     # its second period (2 forecast days)


def _dup_scope(doc):
    doc["scopes"][1]["scope_id"] = doc["scopes"][0]["scope_id"]
    doc["scopes"][1]["scope_type"] = doc["scopes"][0]["scope_type"]


def _two_orgs(doc):
    doc["scopes"][1]["scope_type"] = "org"
    doc["scopes"][1]["scope_id"] = "another-org"


def _series_missing_preset(doc):
    del doc["scopes"][1]["series"]["usage-up-20"]


def _series_extra_preset(doc):
    doc["scopes"][1]["series"]["surprise"] = make_series_entry(1)


def _series_renamed(doc):
    doc["scopes"][2]["series"]["other"] = doc["scopes"][2]["series"].pop("usage-up-20")


def _dup_preset(doc):
    doc["presets"][1]["id"] = "baseline"


def _swap_period_order(doc):
    doc["periods"][0]["from"], doc["periods"][0]["start"] = "2026-10-30", "2026-10-31"


REFUSALS = [
    # ---- schema
    ("schema missing", _delete(["schema"]), "schema"),
    ("schema not a string", _set(["schema"], 1), "must be a string"),
    ("schema null", _set(["schema"], None), "must be a string"),
    ("schema empty", _set(["schema"], ""), "malformed schema"),
    ("schema no minor", _set(["schema"], "dashboard-forecast/1"), "malformed schema"),
    ("schema three parts", _set(["schema"], "dashboard-forecast/1.0.1"), "malformed schema"),
    ("schema other family", _set(["schema"], "other-export/1.0"), "malformed schema"),
    ("schema leading zero", _set(["schema"], "dashboard-forecast/01.0"), "malformed schema"),
    ("schema non ascii digit", _set(["schema"], "dashboard-forecast/١.٠"), "malformed schema"),
    ("schema major 2", _set(["schema"], "dashboard-forecast/2.0"), "unsupported schema version 'dashboard-forecast/2.0'"),
    ("schema major 10", _set(["schema"], "dashboard-forecast/10.1"), "unsupported schema version"),
    ("schema major 0", _set(["schema"], "dashboard-forecast/0.9"), "unsupported schema version 'dashboard-forecast/0.9'"),
    # ---- missing required top-level keys
    *[(f"missing {key}", _delete([key]), f"missing required key '{key}'")
      for key in ("generated_at", "timezone", "origin", "data_through", "band", "quantile_grid",
                  "dates", "periods", "presets", "scopes")],
    # ---- generated_at / timezone
    ("generated_at out of range", _set(["generated_at"], "0001-01-01T00:00:00+05:00"), "out of range"),
    ("schema with a trailing newline", _set(["schema"], "dashboard-forecast/1.0\n"), "malformed schema"),
    ("date with a trailing newline", _set(["origin"], "2026-09-30\n"), "origin"),
    ("generated_at not a string", _set(["generated_at"], 5), "generated_at"),
    ("generated_at garbage", _set(["generated_at"], "yesterday"), "not an ISO-8601 timestamp"),
    ("generated_at naive", _set(["generated_at"], "2026-10-31T10:00:00"), "UTC offset"),
    ("generated_at date only", _set(["generated_at"], "2026-10-31"), "UTC offset"),
    ("timezone empty", _set(["timezone"], ""), "timezone"),
    ("timezone not a string", _set(["timezone"], 330), "timezone"),
    # ---- origin / data_through
    ("origin not a string", _set(["origin"], 20260930), "origin"),
    ("origin wrong format", _set(["origin"], "2026/09/30"), "ISO date"),
    ("origin basic format", _set(["origin"], "20260930"), "ISO date"),
    ("origin week date", _set(["origin"], "2026-W40-3"), "ISO date"),
    ("origin not a calendar date", _set(["origin"], "2026-13-01"), "not a calendar date"),
    ("data_through feb 30", _set(["data_through"], "2026-02-30"), "not a calendar date"),
    ("origin after data_through", _set(["origin"], "2026-11-01"), "must not be after data_through"),
    # ---- band and grid
    ("band not an object", _set(["band"], [10, 90]), "band"),
    ("band low missing", _delete(["band", "low"]), "missing required key 'low'"),
    ("band high missing", _delete(["band", "high"]), "missing required key 'high'"),
    ("band low a string", _set(["band", "low"], "10"), "must be integers"),
    ("band low a float", _set(["band", "low"], 10.0), "must be integers"),
    ("band low a bool", _set(["band", "low"], True), "must be integers"),
    ("band low not in grid", _set(["band", "low"], 12), "not an entry of quantile_grid"),
    ("band high not in grid", _set(["band", "high"], 91), "not an entry of quantile_grid"),
    ("band low equals high", _set(["band", "high"], 10), "must be below"),
    ("band low above high", _set(["band"], {"low": 90, "high": 10}), "must be below"),
    ("grid not a list", _set(["quantile_grid"], "0..100"), "quantile_grid"),
    ("grid too short", _set(["quantile_grid"], [0]), "at least two"),
    ("grid has a float", _set(["quantile_grid", 3], 15.0), "list of at least two integers"),
    ("grid has a bool", _set(["quantile_grid", 3], True), "list of at least two integers"),
    ("grid has a string", _set(["quantile_grid", 3], "15"), "list of at least two integers"),
    ("grid does not start at 0", _set(["quantile_grid", 0], 1), "from 0 to 100"),
    ("grid does not end at 100", _set(["quantile_grid", 20], 99), "from 0 to 100"),
    ("grid not increasing", _set(["quantile_grid", 5], 20), "strictly increasing"),
    ("grid decreasing", _set(["quantile_grid", 5], 10), "strictly increasing"),
    # ---- dates
    ("dates not a list", _set(["dates"], "2026-10-30"), "dates"),
    ("dates empty", _set(["dates"], []), "dates: must not be empty"),
    ("dates entry not a string", _set(["dates", 1], 20261031), "dates[1]"),
    ("dates entry malformed", _set(["dates", 1], "31-10-2026"), "dates[1]"),
    ("dates not consecutive", _set(["dates", 2], "2026-11-03"), "not consecutive at index 2"),
    ("dates descending", _set(["dates"], ["2026-11-02", "2026-11-01", "2026-10-31", "2026-10-30"]),
     "not consecutive"),
    ("dates repeated", _set(["dates", 1], "2026-10-30"), "not consecutive"),
    # ---- periods
    ("periods not a list", _set(["periods"], {}), "periods"),
    ("periods empty", _set(["periods"], []), "periods: must not be empty"),
    ("period not an object", _set(["periods", 0], "2026-10"), "periods[0]"),
    ("period start missing", _delete(["periods", 0, "start"]), "missing required key 'start'"),
    ("period end missing", _delete(["periods", 1, "end"]), "missing required key 'end'"),
    ("period from missing", _delete(["periods", 0, "from"]), "missing required key 'from'"),
    ("period from before start", _set(["periods", 1, "from"], "2026-10-31"), "must lie between"),
    ("period from after end", _set(["periods", 0, "start"], "2026-11-05"), "must lie between"),
    ("period from outside start..end", _set(["periods", 0, "end"], "2026-10-30"), "must lie between"),
    ("period from not a date", _set(["periods", 0, "from"], "soon"), "periods[0].from"),
    ("period from not on the date axis", _set(["periods", 0, "from"], "2026-10-15"),
     "periods[0].from: '2026-10-15' is not one of the export's dates"),
    ("period end not on the date axis", _set(["periods", 1, "end"], "2026-11-30"),
     "periods[1].end: '2026-11-30' is not one of the export's dates"),
    # ---- presets
    ("presets not a list", _set(["presets"], {}), "presets"),
    ("presets empty", _set(["presets"], []), "presets: must not be empty"),
    ("preset not an object", _set(["presets", 0], "baseline"), "presets[0]"),
    ("preset id missing", _delete(["presets", 0, "id"]), "missing required key 'id'"),
    ("preset id empty", _set(["presets", 0, "id"], ""), "presets[0].id"),
    ("preset id not a string", _set(["presets", 0, "id"], 7), "presets[0].id"),
    ("preset id duplicated", _dup_preset, "duplicate preset id 'baseline'"),
    ("preset label missing", _delete(["presets", 1, "label"]), "missing required key 'label'"),
    ("preset label not a string", _set(["presets", 1, "label"], 3), "presets[1].label"),
    # ---- scopes
    ("scopes not a list", _set(["scopes"], {}), "scopes"),
    ("scopes empty", _set(["scopes"], []), "scopes: must not be empty"),
    ("scope not an object", _set(["scopes", 0], "org"), "scopes[0]"),
    ("scope type missing", _delete(["scopes", 0, "scope_type"]), "missing required key 'scope_type'"),
    ("scope type unknown", _set(["scopes", 1, "scope_type"], "project"), "unknown scope type 'project'"),
    ("scope type not a string", _set(["scopes", 1, "scope_type"], 3), "unknown scope type"),
    ("scope id missing", _delete(["scopes", 1, "scope_id"]), "missing required key 'scope_id'"),
    ("scope id empty", _set(["scopes", 1, "scope_id"], ""), "scopes[1].scope_id"),
    ("scope id not a string", _set(["scopes", 1, "scope_id"], 12), "scopes[1].scope_id"),
    ("scope name missing", _delete(["scopes", 1, "name"]), "missing required key 'name'"),
    ("scope name empty", _set(["scopes", 1, "name"], ""), "scopes[1].name"),
    ("scope duplicated", _dup_scope, "duplicate scope"),
    ("two org scopes", _two_orgs, "more than one 'org' scope"),
    ("series missing", _delete(["scopes", 0, "series"]), "missing required key 'series'"),
    ("series not an object", _set(["scopes", 0, "series"], []), "scopes[0].series"),
    ("series lacks a preset", _series_missing_preset, "missing ['usage-up-20']"),
    ("series has an extra preset", _series_extra_preset, "unexpected ['surprise']"),
    ("series key renamed", _series_renamed, "keys must be exactly the preset ids"),
    ("series entry not an object", _set(_S0, "complete"), "scopes[0].series['baseline']"),
    # ---- series entries
    ("status missing", _delete(_S0 + ["status"]), "missing required key 'status'"),
    ("status unknown", _set(_S0 + ["status"], "done"), "unknown status 'done'"),
    ("status not a string", _set(_S0 + ["status"], None), "unknown status"),
    ("status case differs", _set(_S0 + ["status"], "Complete"), "unknown status"),
    ("daily missing", _delete(_S0 + ["daily"]), "missing required key 'daily'"),
    ("daily not an object", _set(_S0 + ["daily"], [1, 2, 3, 4]), "daily"),
    ("daily low missing", _delete(_S0 + ["daily", "low"]), "missing required key 'low'"),
    ("daily expected not a list", _set(_S0 + ["daily", "expected"], 5), "daily.expected"),
    ("daily expected too short", _set(_S0 + ["daily", "expected"], [1, 2, 3]),
     "daily.expected: expected 4 entries, got 3"),
    ("daily high too long", _set(_S0 + ["daily", "high"], [1, 2, 3, 4, 5]),
     "daily.high: expected 4 entries, got 5"),
    ("periods missing", _delete(_S0 + ["periods"]), "missing required key 'periods'"),
    ("periods not a list", _set(_S0 + ["periods"], {}), "periods"),
    ("period count too small", _set(_S0 + ["periods"], [make_series_entry(1)["periods"][0]]),
     "expected 2 entries (one per export period), got 1"),
    ("period count too large", lambda d: d["scopes"][0]["series"]["baseline"]["periods"].append({}),
     "expected 2 entries (one per export period), got 3"),
    ("period entry not an object", _set(_P0, []), "periods[0]"),
    ("cumulative missing", _delete(_P0 + ["cumulative"]), "missing required key 'cumulative'"),
    ("cumulative expected missing", _delete(_P0 + ["cumulative", "expected"]),
     "missing required key 'expected'"),
    ("cumulative expected wrong length", _set(_P1 + ["cumulative", "expected"], [1]),
     "cumulative.expected: expected 2 entries, got 1"),
    ("cumulative expected for the wrong period", _set(_P0 + ["cumulative", "expected"], [1, 2]),
     "cumulative.expected: expected 1 entries, got 2"),
    ("cumulative quantiles missing", _delete(_P0 + ["cumulative", "quantiles"]),
     "missing required key 'quantiles'"),
    ("cumulative quantiles one row short", _set(_P1 + ["cumulative", "quantiles"], [[0] * 21]),
     "cumulative.quantiles: expected 2 entries, got 1"),
    ("cumulative quantile row too short", _set(_P1 + ["cumulative", "quantiles", 1], [0] * 20),
     "cumulative.quantiles[1]: expected 21 entries, got 20"),
    ("cumulative quantile row not a list", _set(_P0 + ["cumulative", "quantiles", 0], 7),
     "cumulative.quantiles[0]"),
    ("total missing", _delete(_P0 + ["total"]), "missing required key 'total'"),
    ("total quantiles missing", _delete(_P0 + ["total", "quantiles"]), "missing required key 'quantiles'"),
    ("total quantiles wrong length", _set(_P1 + ["total", "quantiles"], [0] * 22),
     "total.quantiles: expected 21 entries, got 22"),
]


@pytest.mark.parametrize("name, mutate, match", REFUSALS, ids=[r[0] for r in REFUSALS])
def test_structurally_wrong_documents_are_refused_with_a_clear_message(tmp_path, name, mutate, match):
    doc = make_export()
    mutate(doc)
    path = write_export(tmp_path / "e.json", doc)
    with pytest.raises(ForecastLoadError) as exc:
        parse_export(path)
    assert match in str(exc.value)          # a plain substring, not a pattern


def test_the_unmodified_document_passes_the_same_harness(tmp_path):
    """Guards the parametrized cases above: every refusal must come from its one mutation."""
    parse_export(write_export(tmp_path / "e.json", make_export()))


def test_refused_version_names_the_version_found(tmp_path):
    doc = make_export()
    doc["schema"] = "dashboard-forecast/3.4"
    with pytest.raises(ForecastLoadError) as exc:
        parse_export(write_export(tmp_path / "e.json", doc))
    assert "dashboard-forecast/3.4" in str(exc.value)


def test_an_incomplete_entry_needs_no_numbers(tmp_path):
    doc = make_export()
    doc["scopes"][0]["series"]["baseline"] = {"status": "incomplete"}
    parse_export(write_export(tmp_path / "e.json", doc))


# ------------------------------------------------------------- dataset cross-check (pure)

def _parsed(tmp_path, specs=None):
    return parse_export(write_export(tmp_path / "e.json", make_export(specs)))


def test_check_against_dataset_accepts_a_matching_export(tmp_path):
    check_against_dataset(_parsed(tmp_path), org_id="default", team_ids={"development", "qa"},
                          repo_ids={"github:acme/auth-service", "github:acme/other"})


def test_check_against_dataset_names_every_unknown_id(tmp_path):
    parsed = _parsed(tmp_path)
    with pytest.raises(ForecastLoadError) as exc:
        check_against_dataset(parsed, org_id="elsewhere", team_ids={"qa"}, repo_ids=set())
    message = str(exc.value)
    assert "'default'" in message and "'elsewhere'" in message
    assert "team 'development'" in message
    assert "repo 'github:acme/auth-service'" in message


def test_check_against_dataset_without_an_org_scope_only_checks_teams_and_repos(tmp_path):
    parsed = _parsed(tmp_path, [("team", "qa", "QA")])
    check_against_dataset(parsed, org_id="anything", team_ids={"qa"}, repo_ids=set())
    with pytest.raises(ForecastLoadError, match="team 'qa'"):
        check_against_dataset(parsed, org_id="anything", team_ids=set(), repo_ids=set())


# ==================================================================================== guard

def test_the_synth_guard_refuses_a_non_synth_database_before_any_connection(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("must not connect")

    monkeypatch.setattr(forecast_load, "get_engine", boom)
    monkeypatch.setattr(forecast_load.dbm_operations, "migrate", boom)
    monkeypatch.setattr(forecast_load.QueryCatalog, "load", boom)
    # the file does not exist either: the guard comes first, before the file is even read
    for dsn in ("postgresql+psycopg://u@h:5433/econ", "postgresql+psycopg://u@h:5433/econ_synth_x",
                "postgresql+psycopg://u@h:5433/"):
        with pytest.raises(ForecastLoadError, match="_synth"):
            load_forecast_export(tmp_path / "missing.json", dsn)


def test_an_unparseable_dsn_is_refused(tmp_path):
    with pytest.raises(ForecastLoadError, match="cannot parse dsn"):
        load_forecast_export(tmp_path / "missing.json", "not-a-dsn-at-all")


def test_a_malformed_file_never_reaches_the_database(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("must not connect")

    monkeypatch.setattr(forecast_load, "get_engine", boom)
    monkeypatch.setattr(forecast_load.dbm_operations, "migrate", boom)
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(ForecastLoadError, match="not valid JSON"):
        load_forecast_export(bad, "postgresql+psycopg://u@h:5433/x_synth")


def test_replace_run_rejects_a_zero_batch_size():
    with pytest.raises(ValueError, match="batch_size"):
        replace_run(None, None, None, batch_size=0)


# ================================================================================= database

@pytest.fixture
def db(cfg):
    """A database at head with empty forecast tables, plus our own engine for assertions."""
    operations.init(cfg, force=True)
    dispose_engine()
    eng = create_engine(cfg.dsn)
    yield eng
    eng.dispose()
    dispose_engine()


@pytest.fixture
def catalog():
    return QueryCatalog.load("config/queries")


def _replace(eng, catalog, parsed, **kwargs):
    """One caller-owned transaction around replace_run."""
    with Session(eng) as s, s.begin():
        return replace_run(s, catalog, parsed, **kwargs)


def _counts(eng):
    with eng.connect() as conn:
        return (conn.execute(text("SELECT count(*) FROM forecast_runs")).scalar_one(),
                conn.execute(text("SELECT count(*) FROM forecast_scope_series")).scalar_one())


@requires_db
def test_replace_run_stores_exactly_the_expected_rows(db, catalog, tmp_path):
    doc = make_export()
    path = write_export(tmp_path / "e.json", doc)
    parsed = parse_export(path)
    assert _replace(db, catalog, parsed) == 6

    dump = dump_forecast_tables(db)
    assert len(dump["runs"]) == 1
    run_id, origin, data_through, generated_at, meta, loaded_at = dump["runs"][0]
    assert run_id == parsed.run_id
    assert origin == date(2026, 9, 30) and data_through == date(2026, 10, 30)
    assert generated_at == datetime(2026, 10, 31, 10, 0, 0, tzinfo=timezone.utc)
    assert meta == parsed.meta == json.loads(json.dumps(parsed.meta))
    assert meta["scopes"][1] == {"scope_type": "team", "scope_id": "development", "name": "Development"}
    assert loaded_at.tzinfo is not None
    assert abs((datetime.now(timezone.utc) - loaded_at).total_seconds()) < 120

    assert len(dump["series"]) == 6
    keys = {(r[1], r[2], r[3]) for r in dump["series"]}
    assert keys == {(t, i, p) for t, i in [("org", "default"), ("team", "development"),
                                           ("repo", "github:acme/auth-service")]
                    for p in ("baseline", "usage-up-20")}
    for r_id, scope_type, scope_id, preset_id, status, payload in dump["series"]:
        assert r_id == parsed.run_id and status == "complete"
        assert payload == source_payload(doc, scope_type, scope_id, preset_id)   # the source JSON, exactly


@requires_db
def test_replace_run_stores_incomplete_as_an_empty_payload(db, catalog, tmp_path):
    doc = make_export()
    doc["scopes"][1]["series"]["baseline"] = {"status": "incomplete"}
    parsed = parse_export(write_export(tmp_path / "e.json", doc))
    _replace(db, catalog, parsed)
    with db.connect() as conn:
        row = conn.execute(text(
            "SELECT status, payload FROM forecast_scope_series "
            "WHERE scope_id = 'development' AND preset_id = 'baseline'")).one()
    assert (row.status, row.payload) == ("incomplete", {})


@requires_db
def test_an_extra_series_key_survives_the_database(db, catalog, tmp_path):
    doc = make_export()
    doc["schema"] = "dashboard-forecast/1.2"
    doc["scopes"][0]["series"]["baseline"]["extra"] = {"k": [1, 2, 3]}
    doc["extra_top"] = [1]
    _replace(db, catalog, parse_export(write_export(tmp_path / "e.json", doc)))
    with db.connect() as conn:
        payload = conn.execute(text(
            "SELECT payload FROM forecast_scope_series WHERE scope_type = 'org' "
            "AND preset_id = 'baseline'")).scalar_one()
        meta = conn.execute(text("SELECT meta FROM forecast_runs")).scalar_one()
    assert payload["extra"] == {"k": [1, 2, 3]} and "status" not in payload
    assert meta["extra_top"] == [1] and meta["schema"] == "dashboard-forecast/1.2"


@requires_db
def test_reloading_the_identical_file_is_idempotent_and_loaded_at_moves(db, catalog, tmp_path):
    parsed = parse_export(write_export(tmp_path / "e.json", make_export()))
    _replace(db, catalog, parsed)
    first = dump_forecast_tables(db)
    time.sleep(0.01)
    _replace(db, catalog, parsed)
    second = dump_forecast_tables(db)

    assert second["series"] == first["series"]
    assert len(second["runs"]) == 1
    before, after = first["runs"][0], second["runs"][0]
    assert before[:5] == after[:5]                 # everything but loaded_at is identical
    assert after[5] > before[5]                    # and loaded_at moved forward


@requires_db
def test_loading_a_second_different_export_leaves_exactly_one_run(db, catalog, tmp_path):
    doc_a = make_export()
    doc_b = make_export([("org", "default", "acme.dev"), ("team", "platform", "Platform")],
                        generated_at="2026-10-31T11:00:00Z")
    a = parse_export(write_export(tmp_path / "a.json", doc_a))
    b = parse_export(write_export(tmp_path / "b.json", doc_b))
    assert a.run_id != b.run_id
    _replace(db, catalog, a)
    assert _counts(db) == (1, 6)
    _replace(db, catalog, b)

    assert _counts(db) == (1, 4)
    dump = dump_forecast_tables(db)
    assert [r[0] for r in dump["runs"]] == [b.run_id]
    assert {r[0] for r in dump["series"]} == {b.run_id}
    assert {(r[1], r[2]) for r in dump["series"]} == {("org", "default"), ("team", "platform")}
    assert not any(r[2] in ("development", "github:acme/auth-service") for r in dump["series"])


@requires_db
def test_replace_run_never_commits_the_caller_owns_the_transaction(db, catalog, tmp_path):
    parsed = parse_export(write_export(tmp_path / "e.json", make_export()))
    with Session(db) as s:
        replace_run(s, catalog, parsed)
        assert s.execute(text("SELECT count(*) FROM forecast_runs")).scalar_one() == 1   # visible inside
        s.rollback()
    assert _counts(db) == (0, 0)


@requires_db
def test_a_failure_in_a_later_batch_rolls_everything_back_and_keeps_the_previous_run(
        db, catalog, tmp_path):
    old = parse_export(write_export(tmp_path / "old.json", make_export()))
    _replace(db, catalog, old)
    before = dump_forecast_tables(db)

    new = parse_export(write_export(tmp_path / "new.json", make_export(
        [("org", "default", "acme.dev"), ("team", "platform", "Platform")],
        generated_at="2026-10-31T11:00:00Z")))
    # six rows in batches of two, then a duplicate of the first row: the primary key fails in
    # the fourth batch, after the run row and three good batches were already written
    broken = dataclasses.replace(new, series=new.series + [new.series[0]])
    assert len(broken.series) == 5
    with pytest.raises(Exception) as exc:
        _replace(db, catalog, broken, batch_size=2)
    assert "forecast_scope_series_pkey" in str(exc.value) or "duplicate key" in str(exc.value)

    assert dump_forecast_tables(db) == before                     # old run intact, byte for byte
    assert [r[0] for r in before["runs"]] == [old.run_id]


@requires_db
def test_a_failure_while_inserting_the_run_row_keeps_the_previous_run(db, catalog, tmp_path):
    old = parse_export(write_export(tmp_path / "old.json", make_export()))
    _replace(db, catalog, old)
    before = dump_forecast_tables(db)
    new = parse_export(write_export(tmp_path / "new.json", make_export(
        [("org", "default", "acme.dev")], generated_at="2026-10-31T11:00:00Z")))
    broken = dataclasses.replace(new, meta={"unserialisable": {1, 2}})
    with pytest.raises(TypeError):
        _replace(db, catalog, broken)
    assert dump_forecast_tables(db) == before


@requires_db
def test_two_concurrent_replaces_are_serialised_and_leave_one_run(db, catalog, tmp_path):
    a = parse_export(write_export(tmp_path / "a.json", make_export()))
    b = parse_export(write_export(tmp_path / "b.json", make_export(
        [("org", "default", "acme.dev")], generated_at="2026-10-31T12:00:00Z")))
    errors: list[BaseException] = []

    def second_loader():
        try:
            _replace(db, catalog, b)
        except BaseException as exc:                                   # noqa: BLE001
            errors.append(exc)

    with Session(db) as first:
        first.begin()
        replace_run(first, catalog, a)                                 # holds the lock, uncommitted
        thread = threading.Thread(target=second_loader)
        thread.start()
        thread.join(timeout=1.0)
        assert thread.is_alive(), "the second loader must wait for the first to commit"
        first.commit()
    thread.join(timeout=20)
    assert not thread.is_alive() and not errors
    dump = dump_forecast_tables(db)
    assert [r[0] for r in dump["runs"]] == [b.run_id]                  # the later load won, alone
    assert {r[0] for r in dump["series"]} == {b.run_id}


@requires_synth_db
def test_load_forecast_export_loads_and_returns_the_summary(db, tmp_path):
    doc = make_export()
    path = write_export(tmp_path / "e.json", doc)
    result = load_forecast_export(path, DSN)
    parsed = parse_export(path)
    assert result.run_id == parsed.run_id
    assert (result.scope_series_rows, result.scope_count, result.preset_count) == (6, 3, 2)
    assert result.origin == date(2026, 9, 30) and result.data_through == date(2026, 10, 30)
    assert result.duration_ms > 0
    dump = dump_forecast_tables(db)
    assert [r[0] for r in dump["runs"]] == [parsed.run_id]
    for _, scope_type, scope_id, preset_id, _, payload in dump["series"]:
        assert payload == source_payload(doc, scope_type, scope_id, preset_id)


@requires_synth_db
def test_load_forecast_export_replaces_the_previous_run(db, tmp_path):
    first = load_forecast_export(write_export(tmp_path / "a.json", make_export()), DSN)
    second = load_forecast_export(write_export(
        tmp_path / "b.json", make_export(generated_at="2026-10-31T10:00:09Z")), DSN)
    assert first.run_id != second.run_id
    assert _counts(db) == (1, 6)
    assert [r[0] for r in dump_forecast_tables(db)["runs"]] == [second.run_id]


@requires_synth_db
@pytest.mark.parametrize("mutate", [
    lambda raw: raw[: len(raw) // 2],                                         # truncated
    lambda raw: raw.replace(b"dashboard-forecast/1.0", b"dashboard-forecast/2.0"),   # bad version
    lambda raw: raw.replace(b'"quantile_grid":[0,', b'"quantile_grid":[1,'),         # bad grid
], ids=["truncated", "bad-version", "bad-grid"])
def test_a_malformed_export_changes_nothing(db, tmp_path, mutate):
    load_forecast_export(write_export(tmp_path / "good.json", make_export()), DSN)
    before = dump_forecast_tables(db)

    bad = tmp_path / "bad.json"
    bad.write_bytes(mutate(_bytes_of(make_export(generated_at="2026-10-31T10:00:09Z"))))
    with pytest.raises(ForecastLoadError):
        load_forecast_export(bad, DSN)
    assert dump_forecast_tables(db) == before                  # full dump, loaded_at included


@requires_synth_db
def test_load_forecast_export_on_a_fresh_database_migrates_first(cfg, tmp_path):
    """The standalone loader runs the migrations itself: a database with no tables at all ends
    with both forecast tables and the loaded run."""
    eng = create_engine(cfg.dsn)
    try:
        operations.init(cfg, force=True)
        dispose_engine()
        from server.storage.schema import Base
        Base.metadata.drop_all(eng)
        with eng.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        result = load_forecast_export(write_export(tmp_path / "e.json", make_export()), DSN)
        assert dump_forecast_tables(eng)["runs"][0][0] == result.run_id
    finally:
        eng.dispose()
        operations.init(cfg, force=True)
        dispose_engine()
