"""Measured resource observations for one forecast invocation.

Sampling is deliberately labeled: transient allocations between samples may be
missed. The separate OS high-water mark covers this process's entire lifetime,
not just the invocation. Importing this module starts no threads or logging.
"""

from __future__ import annotations

from contextlib import contextmanager
import logging
import platform
import resource
import threading
import time
from typing import Iterator

import psutil


class ResourceMonitor:
    """Observe parent/child RSS and CPU without changing application behavior.

    Use ``with ResourceMonitor() as monitor:`` around the entire invocation,
    and ``with monitor.stage('fit'):`` for stage wall times. ``snapshot()`` is
    JSON-safe both during the invocation and after the context exits. Concurrent
    stages accumulate elapsed work and may exceed invocation wall time.
    """

    def __init__(self, interval_seconds: float = 0.05) -> None:
        if not 0 < interval_seconds <= 1:
            raise ValueError("resource sampling interval must be in (0, 1]")
        self.interval_seconds = interval_seconds
        self._process = psutil.Process()
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._started: float | None = None
        self._finished: float | None = None
        self._peak_rss = 0
        self._samples = 0
        self._cpu_baseline: dict[int, tuple[float, float]] = {}
        self._cpu_latest: dict[int, tuple[float, float]] = {}
        self._stage_seconds: dict[str, float] = {}
        self._child_access_limited = False
        self._log = logging.getLogger(__name__)

    def __enter__(self) -> ResourceMonitor:
        if self._started is not None:
            raise RuntimeError("resource monitor cannot be reused")
        self._started = time.perf_counter()
        self._sample(baseline=True)
        self._thread = threading.Thread(
            target=self._observe, name="forecast-resources", daemon=True
        )
        self._thread.start()
        self._log.debug("forecast resource observation started")
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.finish()

    def finish(self) -> dict:
        """Stop observation once and return its final JSON-safe snapshot."""
        if self._started is None:
            raise RuntimeError("resource monitor has not started")
        if self._finished is not None:
            return self.snapshot()
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        self._sample()
        self._finished = time.perf_counter()
        self._log.debug("forecast resource observation finished")
        return self.snapshot()

    def _observe(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self._sample()

    def _sample(self, *, baseline: bool = False) -> None:
        try:
            processes = [self._process, *self._process.children(recursive=True)]
        except (psutil.NoSuchProcess, psutil.AccessDenied, PermissionError):
            processes = [self._process]
            self._child_access_limited = True
        rss = 0
        values: dict[int, tuple[float, float]] = {}
        for process in processes:
            try:
                with process.oneshot():
                    rss += process.memory_info().rss
                    cpu = process.cpu_times()
                    values[process.pid] = (cpu.user, cpu.system)
            except (psutil.NoSuchProcess, psutil.AccessDenied, ProcessLookupError):
                # Children may finish between enumeration and inspection.
                continue
        with self._lock:
            self._peak_rss = max(self._peak_rss, rss)
            self._samples += 1
            self._cpu_latest.update(values)
            if baseline:
                self._cpu_baseline.update(values)

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        if not isinstance(name, str) or not name:
            raise ValueError("resource stage name must be nonempty")
        started = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - started
            with self._lock:
                self._stage_seconds[name] = self._stage_seconds.get(name, 0.0) + elapsed

    def snapshot(self) -> dict:
        """Return measurements, with limitations explicit in machine-readable data."""
        if self._started is None:
            raise RuntimeError("resource monitor has not started")
        if self._finished is None:
            self._sample()
        with self._lock:
            cpu_user = cpu_system = 0.0
            for pid, (user, system) in self._cpu_latest.items():
                base_user, base_system = self._cpu_baseline.get(pid, (0.0, 0.0))
                cpu_user += max(0.0, user - base_user)
                cpu_system += max(0.0, system - base_system)
            high_water = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            high_water_bytes = int(
                high_water * (1 if platform.system() == "Darwin" else 1024)
            )
            return {
                "wall_seconds": (self._finished or time.perf_counter()) - self._started,
                "stage_seconds": dict(self._stage_seconds),
                "peak_rss_bytes": self._peak_rss,
                "memory_measurement": (
                    "sampled parent RSS; child enumeration denied by environment"
                    if self._child_access_limited
                    else "sampled sum of parent and live child RSS"
                ),
                "memory_sample_interval_seconds": self.interval_seconds,
                "memory_sample_count": self._samples,
                "os_process_lifetime_peak_rss_bytes": high_water_bytes,
                "cpu_user_seconds": cpu_user,
                "cpu_system_seconds": cpu_system,
                "cpu_measurement": "parent delta plus observed child CPU; short-lived children may be missed",
            }
