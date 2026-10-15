from datetime import date
import pytest
from forecast.forecasting.errors import InputError
from forecast.forecasting.request import ForecastRequest, resolve_window
from forecast.forecasting.cli_forecast import parser


def test_month_offset_and_leap_year():
    window = resolve_window(ForecastRequest(date(2027, 12, 15), "month", 2, 2))
    assert window.requested_dates[0] == date(2028, 2, 1)
    assert window.requested_dates[-1] == date(2028, 3, 31)


@pytest.mark.parametrize("value", [0, True, 1.0])
def test_request_strict_positive(value):
    with pytest.raises(InputError):
        ForecastRequest(date(2026, 1, 1), "week", value, 1)


@pytest.mark.parametrize(
    "forecast_request",
    [
        ForecastRequest(date(2026, 1, 1), "week", 10**12, 1),
        ForecastRequest(date(2026, 1, 1), "month", 1, 10**12),
        ForecastRequest(date(9999, 12, 31), "month", 1, 1),
    ],
)
def test_unbounded_or_overflowing_windows_reject_before_enumeration(forecast_request):
    with pytest.raises(InputError):
        resolve_window(forecast_request)


def test_cli_help_explains_cutoff_calendar_horizon_and_validation_mode():
    help_text = " ".join(parser().format_help().split())
    for phrase in (
        "defaults to declared dataset end",
        "next calendar week or month",
        "number of consecutive calendar weeks or months",
        "required unless --validate-only",
        "without generating paths or writing output",
        "Reusable modeling and scope policy belongs in the YAML config",
    ):
        assert phrase in help_text
