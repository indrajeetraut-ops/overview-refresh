"""The standalone artifact reader must not import the forecasting stack."""

import json
import os
from pathlib import Path
import subprocess
import sys


def _environment():
    root = Path(__file__).parents[3]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(root / "econ-forecast" / "src"), str(root / "src")]
    )
    return env


def test_artifact_reader_import_is_independent_of_runner_and_statsmodels():
    code = """
import json, sys
import forecast.forecasting.artifacts
print(json.dumps(sorted(name for name in sys.modules if name == 'forecast.forecasting.runner' or name.startswith('statsmodels.tsa'))))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        text=True,
        capture_output=True,
        env=_environment(),
    )
    assert json.loads(completed.stdout) == []


def test_lazy_public_runner_api_remains_available():
    code = """
from forecast.forecasting import ForecastResult, run_forecast
print(ForecastResult.__name__, callable(run_forecast))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        text=True,
        capture_output=True,
        env=_environment(),
    )
    assert completed.stdout.strip() == "ForecastResult True"
