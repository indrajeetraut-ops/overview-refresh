"""The shipped `config/api.yaml` carries the cost-based seat settings
(docs/plan/2026-09-26-cost-based-seat-utilization.md §3.1).

`UtilizationConfig` ignores unknown keys (no `extra="forbid"`), so a stale
`room_to_save_usd_micros` left in a deployed file would be dropped silently and the defaults
would apply without anyone noticing. Loading the real file pins what it actually says.
"""
from server.api.config import UtilizationConfig, load_config


def test_shipped_api_yaml_has_the_cost_based_seat_settings():
    util = load_config("config/api.yaml").utilization
    assert util.over == 0.95
    assert util.under == 0.40
    assert util.new_seat_grace_days == 14
    assert util.room_to_save.min_share == 0.10
    assert util.room_to_save.min_usd_per_30d == 10


def test_the_flat_dollar_room_to_save_cutoff_is_gone():
    assert "room_to_save_usd_micros" not in UtilizationConfig.model_fields
    text = open("config/api.yaml", encoding="utf-8").read()
    assert "room_to_save_usd_micros" not in text
