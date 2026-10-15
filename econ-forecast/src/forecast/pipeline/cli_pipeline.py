"""Workspace pipeline (design `plot-forecast-enhancements-design.md` §14.4).

`utils/pipeline.sh --config <workspace>/pipeline.yaml`. A dumb orchestrator: it
knows the workspace layout (§14.2), calls the module CLIs (`utils/*.sh`) as
subprocesses with the workspace as their working directory, and holds no
module settings. A stage block in `pipeline.yaml` is passed through verbatim to
that stage's commands as `--<key> <value>` (scalars only).

Stages run in the fixed order forecast, cost, plot; the first command that
exits non-zero (`2` included) stops the run and everything after it is
recorded as `skipped`. `<workspace>/pipeline_result.json` is written on every
run -- configuration errors included -- and printed as one JSON line on stdout.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Union

import structlog
import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr, ValidationError, field_validator

from common.msging.log import configure_logging, get_logger

#: Where the module wrappers live (`econ-forecast/utils/`).
UTILS_DIR = Path(__file__).resolve().parents[3] / "utils"
STAGE_ORDER = ("forecast", "cost", "plot")
RESERVED_SCENARIO_STEMS = frozenset({"baseline", "scenarios"})
RESULT_NAME = "pipeline_result.json"
_STDERR_TAIL_LINES = 50
_SCHEMA_VERSION = 1
# Flags the layout supplies; a stage block may not set them (§14.4 rule).
_LAYOUT_FLAGS = frozenset({"config", "output", "force", "forecast-run", "scenario", "cost-run", "artifact"})

_LOG = get_logger("forecast.pipeline")

Scalar = Union[StrictStr, StrictInt, StrictFloat]


class PipelineConfigError(Exception):
    """Unreadable `pipeline.yaml`, unknown stage, reserved scenario stem, ..."""


class PipelineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    stages: list[Literal["forecast", "cost", "plot"]] = Field(min_length=1)
    forecast: dict[str, Scalar] | None = None
    cost: dict[str, Scalar] | None = None
    plot: dict[str, Scalar] | None = None

    @field_validator("stages")
    @classmethod
    def _unique(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("stages must not repeat")
        return value

    @field_validator("forecast", "cost", "plot")
    @classmethod
    def _flag_names(cls, value):
        for key in value or {}:
            if not key or key.startswith("-") or any(ch.isspace() for ch in key):
                raise ValueError(f"invalid flag name {key!r}")
            if key in _LAYOUT_FLAGS:
                raise ValueError(f"--{key} is supplied by the workspace layout; remove it")
        return value


def load_pipeline_config(path: Path) -> PipelineConfig:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise PipelineConfigError("pipeline configuration must be a YAML mapping")
        return PipelineConfig.model_validate(raw)
    except PipelineConfigError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError, ValidationError) as exc:
        raise PipelineConfigError(f"invalid pipeline configuration {path}: {exc}") from exc


def scenario_files(workspace: Path) -> list[Path]:
    directory = workspace / "cost" / "scenarios"
    return sorted(directory.glob("*.yaml")) if directory.is_dir() else []


def check_reserved_scenarios(workspace: Path) -> None:
    reserved = [p.name for p in scenario_files(workspace) if p.stem in RESERVED_SCENARIO_STEMS]
    if reserved:
        raise PipelineConfigError(
            f"reserved scenario file name(s) {reserved}: 'baseline' and 'scenarios' would "
            "overwrite cost/baseline or cost/scenarios")


def _flags(block: dict | None) -> list[str]:
    result: list[str] = []
    for key, value in (block or {}).items():
        result += [f"--{key}", str(value)]
    return result


def _cost_run_dirs(workspace: Path) -> list[str]:
    cost = workspace / "cost"
    if not cost.is_dir():
        return []
    names = sorted(p.name for p in cost.iterdir() if p.is_dir() and p.name != "scenarios")
    return sorted(names, key=lambda name: name != "baseline")


def stage_commands(stage: str, workspace: Path, config: PipelineConfig) -> list[list[str]]:
    """The §14.4 table; argv[0] is the wrapper as recorded (`utils/<name>.sh`)."""
    extra = _flags(getattr(config, stage))
    if stage == "forecast":
        return [["utils/forecast.sh", "--config", "forecast/forecast.yaml",
                 "--output", "forecast/run", "--force", *extra]]
    if stage == "cost":
        commands = [["utils/cost.sh", "--forecast-run", "forecast/run",
                     "--output", "cost/baseline", "--force", *extra]]
        for scenario in scenario_files(workspace):
            commands.append(["utils/cost.sh", "--forecast-run", "forecast/run",
                             "--scenario", f"cost/scenarios/{scenario.name}",
                             "--output", f"cost/{scenario.stem}", "--force", *extra])
        return commands
    commands = [["utils/plot_forecast.sh", "--artifact", "forecast/run",
                 "--config", "plot.yaml", "--force", *extra]]
    for run in _cost_run_dirs(workspace):
        commands.append(["utils/plot_cost.sh", "--cost-run", f"cost/{run}",
                         "--config", "plot.yaml", "--force", *extra])
    return commands


def _parse_summary(stdout: str):
    for line in reversed(stdout.splitlines()):
        if line.strip():
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                return None
    return None


def run_command(argv: list[str], workspace: Path) -> dict:
    """Run one wrapper with cwd = workspace; stream its stderr through, keep the
    last lines, parse its stdout JSON line as `summary`."""
    executable = UTILS_DIR / Path(argv[0]).name
    tail: deque[str] = deque(maxlen=_STDERR_TAIL_LINES)
    started = time.perf_counter()
    _LOG.info("pipeline.command.started", argv=argv)
    try:
        process = subprocess.Popen([str(executable), *argv[1:]], cwd=workspace, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        _LOG.error("pipeline.command.failed", argv=argv, message=str(exc))
        return {"argv": argv, "status": "failed", "exit_code": 127,
                "elapsed_seconds": time.perf_counter() - started, "summary": None,
                "stderr_tail": [f"cannot start {executable}: {exc}"]}
    stdout_chunks: list[str] = []
    reader = threading.Thread(target=lambda: stdout_chunks.append(process.stdout.read()), daemon=True)
    reader.start()
    for line in process.stderr:
        sys.stderr.write(line)
        sys.stderr.flush()
        tail.append(line.rstrip("\n"))
    exit_code = process.wait()
    reader.join()
    elapsed = time.perf_counter() - started
    status = "complete" if exit_code == 0 else "failed"
    log = _LOG.info if exit_code == 0 else _LOG.error
    log(f"pipeline.command.{status}", argv=argv, exit_code=exit_code, elapsed_seconds=elapsed)
    return {"argv": argv, "status": status, "exit_code": exit_code, "elapsed_seconds": elapsed,
            "summary": _parse_summary("".join(stdout_chunks)),
            "stderr_tail": None if exit_code == 0 else list(tail)}


def _skipped(argv: list[str]) -> dict:
    return {"argv": argv, "status": "skipped", "exit_code": None, "elapsed_seconds": None,
            "summary": None, "stderr_tail": None}


def run_pipeline(workspace: Path, config: PipelineConfig) -> tuple[list[dict], int]:
    stages: list[dict] = []
    exit_code = 0
    for stage in (name for name in STAGE_ORDER if name in config.stages):
        commands = stage_commands(stage, workspace, config)
        if exit_code != 0:
            stages.append({"stage": stage, "status": "skipped",
                           "commands": [_skipped(argv) for argv in commands]})
            continue
        _LOG.info("pipeline.stage.started", stage=stage, commands=len(commands))
        records = []
        for argv in commands:
            if exit_code != 0:
                records.append(_skipped(argv))
                continue
            record = run_command(argv, workspace)
            records.append(record)
            if record["exit_code"] != 0:
                exit_code = record["exit_code"]
        status = "complete" if exit_code == 0 else "failed"
        _LOG.info("pipeline.stage.completed", stage=stage, status=status)
        stages.append({"stage": stage, "status": status, "commands": records})
    return stages, exit_code


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        print(json.dumps({"event": "pipeline.failed", "error_type": "input", "message": message},
                         sort_keys=True), file=sys.stderr)
        raise SystemExit(1)


def parser() -> argparse.ArgumentParser:
    result = _Parser(
        prog="pipeline.sh",
        description=("Run a workspace's stages (forecast, cost, plot) through the module CLIs. "
                     "The workspace is the directory holding pipeline.yaml; every command runs "
                     "there. Writes <workspace>/pipeline_result.json on every run."),
    )
    result.add_argument("--config", type=Path, required=True,
                        help="required: <workspace>/pipeline.yaml")
    return result


def _finish(result: dict, workspace: Path | None, started: float) -> None:
    result["elapsed_seconds"] = time.perf_counter() - started
    if workspace is not None and workspace.is_dir():
        try:
            (workspace / RESULT_NAME).write_text(
                json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            _LOG.error("pipeline.result_write_failed", message=str(exc))
    print(json.dumps(result, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    configure_logging(json=True, logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    arguments = parser().parse_args(argv)
    started = time.perf_counter()
    result: dict = {"schema_version": _SCHEMA_VERSION, "status": "failed",
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "elapsed_seconds": 0.0, "stages": []}
    workspace = Path(arguments.config).resolve().parent
    try:
        config = load_pipeline_config(arguments.config)
        check_reserved_scenarios(workspace)
    except PipelineConfigError as exc:
        _LOG.error("pipeline.config_error", config=str(arguments.config), message=str(exc))
        result["error"] = {"type": "input", "message": str(exc)}
        _finish(result, workspace, started)
        return 1
    _LOG.info("pipeline.started", workspace=str(workspace), stages=config.stages)
    stages, exit_code = run_pipeline(workspace, config)
    result.update(status="complete" if exit_code == 0 else "failed", stages=stages)
    _finish(result, workspace, started)
    _LOG.info("pipeline.completed", status=result["status"], exit_code=exit_code,
              elapsed_seconds=result["elapsed_seconds"])
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
