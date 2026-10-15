"""Manager-endpoint latency probe (api-latency plan §0/§2.4/§5.4).

This is the script §0's numbers came from. Run it against a live container to measure the
sync-handler/multi-worker fix, and again after each PR to check the §5.4 targets. It hits real
HTTP, so it is deliberately outside `src/` -- nothing there imports it.

Usage (from the repo root, or piped into a container's python):
    utils/api_latency.py --base-url http://localhost:8090 --viewer <manager user id> \
        --window 15d [--mode sequential|concurrent|both]

    # run against a container directly, e.g. from the AWS host:
    docker exec -i talium-aws-api_synth-1 python - --viewer github:vp-eng --window 15d \
        < utils/api_latency.py

`--viewer` is sent as the `x-talium-viewer` trusted header (`server/directory/service.py`'s
`TRUSTED_VIEWER_HEADER`) that `gate.resolve` reads -- the same header Caddy's basic-auth-gated
UI relies on the backing app to set. `--window` is any width `Nd` (days); `--to` defaults to now.

Two modes:
  sequential  -- one call at a time, each fully awaited before the next starts. This is what
                 a cold single-tab load looks like once the event loop isn't shared.
  concurrent  -- every call fired at once (a `ThreadPoolExecutor`, since httpx's sync client is
                 what a curl-style probe would use). This is what §0.1 found: with `async def`
                 handlers blocking the loop, concurrent calls took about the *sum* of their
                 sequential times instead of running in parallel.

Output is one line per call: elapsed ms, response size in KB, and the path. `--mode both` (the
default) runs sequential then concurrent and prints the concurrent/sequential-sum ratio, which
is the number the org-screen acceptance target (§2.4) is checked against.
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx

#: The org screen's concurrent load: three heavy manager aggregates plus the timezone check
#: fired alongside them (§2.4's "org screen's four parallel calls"). `--paths` overrides this.
_DEFAULT_PATHS = (
    "/v1/org/overview",
    "/v1/teams",
    "/v1/teams/development",
    "/v1/users/{viewer}/timezone",
)


@dataclass
class CallResult:
    path: str
    status_code: int
    elapsed_ms: float
    size_kb: float


def _parse_window(window: str) -> timedelta:
    if not window.endswith("d"):
        raise argparse.ArgumentTypeError(f"window must look like '15d', got {window!r}")
    return timedelta(days=int(window[:-1]))


def _build_paths(paths: list[str], viewer: str, frm: datetime, to: datetime) -> list[str]:
    # quote(..., safe="") -- a raw "+" in the query string means a literal space to the
    # server's query parser, which turns an ISO timestamp's "+00:00" offset into "+  00:00"
    # and 422s every windowed call.
    from_iso, to_iso = quote(frm.isoformat(), safe=""), quote(to.isoformat(), safe="")
    resolved = []
    for p in paths:
        p = p.format(viewer=viewer)
        sep = "&" if "?" in p else "?"
        if p.rstrip("/").endswith("timezone") or p.rstrip("/").endswith("healthz"):
            resolved.append(p)
        else:
            resolved.append(f"{p}{sep}from={from_iso}&to={to_iso}")
    return resolved


def _call(client: httpx.Client, path: str) -> CallResult:
    start = time.monotonic()
    resp = client.get(path)
    elapsed_ms = (time.monotonic() - start) * 1000
    return CallResult(path, resp.status_code, elapsed_ms, len(resp.content) / 1024)


def _report(label: str, results: list[CallResult]) -> None:
    print(f"-- {label} --")
    for r in results:
        print(f"  {r.elapsed_ms:8.1f} ms  {r.size_kb:8.1f} KB  [{r.status_code}]  {r.path}")


def run(base_url: str, viewer: str, paths: list[str], mode: str) -> None:
    headers = {"x-talium-viewer": viewer}
    with httpx.Client(base_url=base_url, headers=headers, timeout=60.0) as client:
        if mode in ("sequential", "both"):
            sequential = [_call(client, p) for p in paths]
            _report("sequential", sequential)
            sequential_sum_ms = sum(r.elapsed_ms for r in sequential)
            slowest_alone_ms = max(r.elapsed_ms for r in sequential)
        else:
            sequential_sum_ms = slowest_alone_ms = None

        if mode in ("concurrent", "both"):
            with ThreadPoolExecutor(max_workers=len(paths)) as pool:
                start = time.monotonic()
                concurrent = list(pool.map(lambda p: _call(client, p), paths))
                wall_ms = (time.monotonic() - start) * 1000
            _report("concurrent", concurrent)
            print(f"  wall clock: {wall_ms:.1f} ms")
            if slowest_alone_ms:
                print(f"  ratio to slowest-alone: {wall_ms / slowest_alone_ms:.2f}x "
                      f"(§2.4 target: <= 1.3x)")
            if sequential_sum_ms:
                print(f"  ratio to sequential sum: {wall_ms / sequential_sum_ms:.2f}x")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:8090")
    parser.add_argument("--viewer", required=True, help="x-talium-viewer header value")
    parser.add_argument("--window", default="15d", type=_parse_window)
    parser.add_argument("--to", default=None, help="ISO timestamp; defaults to now (UTC)")
    parser.add_argument("--mode", choices=["sequential", "concurrent", "both"], default="both")
    parser.add_argument("--paths", nargs="*", default=list(_DEFAULT_PATHS),
                        help="override the default org-screen path set; '{viewer}' is "
                             "substituted with --viewer")
    args = parser.parse_args()

    to = datetime.fromisoformat(args.to) if args.to else datetime.now(timezone.utc)
    frm = to - args.window
    paths = _build_paths(args.paths, args.viewer, frm, to)
    run(args.base_url, args.viewer, paths, args.mode)


if __name__ == "__main__":
    main()
