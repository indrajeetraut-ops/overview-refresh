"""Negative input and output-ownership cases from the frozen contract."""

from datetime import date

import pytest

from forecast.forecasting.dataset import load_dataset
from forecast.forecasting.errors import InputError
from forecast.forecasting.request import ForecastRequest
from forecast.forecasting.runner import run_forecast


@pytest.mark.parametrize("null_index", [0, 1, 2])
def test_null_token_category_is_invalid_instead_of_zero(
    null_index, tmp_path, daily_dataset_factory
):
    def token_values(*_):
        values = [100, 200, 50]
        values[null_index] = None
        return tuple(values)

    dataset = daily_dataset_factory(tmp_path / "null-token", token_fn=token_values)
    with pytest.raises(InputError, match="null tokens"):
        load_dataset(dataset)


@pytest.mark.parametrize("origin", [date(2026, 6, 30), date(2026, 8, 30)])
def test_origin_outside_declared_dataset_interval_is_invalid(
    origin, tmp_path, daily_dataset_factory, forecast_config_factory
):
    dataset = daily_dataset_factory(tmp_path / "daily")
    config = forecast_config_factory(tmp_path / "forecast.yaml", dataset)
    with pytest.raises(InputError, match="outside declared dataset interval"):
        run_forecast(config, ForecastRequest(origin, "month"), validate_only=True)


def test_existing_output_directory_and_sentinel_are_byte_preserved(
    tmp_path, daily_dataset_factory, forecast_config_factory
):
    dataset = daily_dataset_factory(tmp_path / "daily")
    config = forecast_config_factory(tmp_path / "forecast.yaml", dataset)
    output = tmp_path / "existing-output"
    output.mkdir()
    sentinel = output / "sentinel.bin"
    expected = b"preexisting bytes\x00must survive\n"
    sentinel.write_bytes(expected)

    with pytest.raises(InputError, match="output path already exists"):
        run_forecast(
            config,
            ForecastRequest(date(2026, 8, 29), "month"),
            output,
        )

    assert output.is_dir()
    assert sentinel.read_bytes() == expected
    assert sorted(path.name for path in output.iterdir()) == ["sentinel.bin"]
