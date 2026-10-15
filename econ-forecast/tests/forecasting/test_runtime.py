"""Independent resource-accounting oracles, including disappearing children."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from forecast.forecasting.runtime import ResourceMonitor


class FakeProcess:
    def __init__(self, pid, rss, user, system):
        self.pid, self.rss, self.user, self.system = pid, rss, user, system
        self.descendants = []

    def children(self, recursive):
        return self.descendants

    def oneshot(self):
        return nullcontext()

    def memory_info(self):
        return SimpleNamespace(rss=self.rss)

    def cpu_times(self):
        return SimpleNamespace(user=self.user, system=self.system)


def test_process_tree_peak_and_cpu_delta_survive_child_exit():
    parent = FakeProcess(1, 100, 10, 2)
    child = FakeProcess(2, 50, 4, 1)
    parent.descendants = [child]
    monitor = ResourceMonitor(interval_seconds=1)
    monitor._process = parent
    with monitor:
        parent.user, parent.system, parent.rss = 13, 3, 120
        child.user, child.system, child.rss = 6, 2, 60
        first = monitor.snapshot()
        assert first["peak_rss_bytes"] == 180
        assert first["cpu_user_seconds"] == 5
        assert first["cpu_system_seconds"] == 2
        parent.descendants = []
    final = monitor.snapshot()
    assert final["peak_rss_bytes"] == 180
    assert final["cpu_user_seconds"] == 5
    assert final["memory_sample_count"] >= 3
    assert final["wall_seconds"] >= first["wall_seconds"]
    assert monitor._thread is not None and not monitor._thread.is_alive()


def test_failed_stage_is_timed_and_monitor_stops():
    monitor = ResourceMonitor()
    with pytest.raises(RuntimeError, match="deliberate"):
        with monitor:
            with monitor.stage("write"):
                raise RuntimeError("deliberate write failure")
    result = monitor.snapshot()
    assert result["stage_seconds"]["write"] >= 0
    assert result["peak_rss_bytes"] > 0
    assert result["os_process_lifetime_peak_rss_bytes"] > 0
    assert "sampled" in result["memory_measurement"]
    assert not monitor._thread.is_alive()


@pytest.mark.parametrize("interval", [0, -0.1, 2, float("nan"), float("inf")])
def test_invalid_sampling_interval(interval):
    with pytest.raises(ValueError):
        ResourceMonitor(interval)


def test_monitor_lifecycle_errors_are_explicit():
    monitor = ResourceMonitor()
    with pytest.raises(RuntimeError, match="not started"):
        monitor.snapshot()
    with monitor:
        with pytest.raises(ValueError):
            with monitor.stage(""):
                pass
    with pytest.raises(RuntimeError, match="reused"):
        with monitor:
            pass
