"""Response parity snapshot across the api-latency refactor (plan §5.2).

Calls every manager GET endpoint, over three windows, for the org, every team, the first few
people, every project, one PR and one feature -- and writes each response as sorted-key JSON to
a directory. Run it on the pre-change commit, again after PR 2, again after PR 3, and `diff -r`
the three directories: they must be identical except for `session_types` on synthetic data
(Phase 0's turns-as-a-count fix touches 12 of 14,389 sessions there; real data has zero diffs).

Usage:
    utils/api_parity.py --base-url http://localhost:8090 --viewer <manager user id> \
        --out snapshots/before

    diff -r snapshots/before snapshots/after-pr2
    diff -r snapshots/before snapshots/after-pr3

`--viewer` is the `x-talium-viewer` trusted header every manager route gates on
(`server/directory/service.py`'s `TRUSTED_VIEWER_HEADER`) -- it must resolve to a lead/admin
(`gate.require_manager`), same as `utils/api_latency.py`.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

#: Explicit from/to, not `period=` (aggregate.PERIOD_DAYS only names 15d/30d/60d, no 7d) --
#: computed against the one `--to` anchor every call in a snapshot shares, so `diff -r` across
#: runs compares the same windows rather than each run's own "now".
_WINDOW_DAYS = (7, 30, 60)


def _slug(text: str) -> str:
    """A filesystem-safe stand-in for a scope id/path (e.g. `github:owner/name` -> one file
    name), collision-free enough for this dev tool's own directory."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_") or "root"


def _get(client: httpx.Client, path: str, params: dict) -> dict | None:
    resp = client.get(path, params=params)
    if resp.status_code != 200:
        print(f"  ! {resp.status_code} {path} {params}", file=sys.stderr)
        return None
    return resp.json()


def _write(out_dir: Path, name: str, payload: dict | None) -> None:
    if payload is None:
        return
    path = out_dir / f"{name}.json"
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")


def snapshot(base_url: str, viewer: str, out_dir: Path, to: datetime) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    headers = {"x-talium-viewer": viewer}
    windows = {f"{days}d": {"from": (to - timedelta(days=days)).isoformat(), "to": to.isoformat()}
              for days in _WINDOW_DAYS}
    with httpx.Client(base_url=base_url, headers=headers, timeout=120.0) as client:
        scopes = _get(client, "/v1/scopes", {})
        teams = scopes["teams"] if scopes else []
        projects = scopes["projects"] if scopes else []

        for label, w in windows.items():
            _write(out_dir, f"org_overview_{label}", _get(client, "/v1/org/overview", w))
            _write(out_dir, f"org_usage_{label}", _get(client, "/v1/org/usage", w))
            _write(out_dir, f"teams_table_{label}", _get(client, "/v1/teams", w))
            _write(out_dir, f"projects_table_{label}", _get(client, "/v1/projects", w))
            _write(out_dir, f"economics_org_{label}",
                  _get(client, "/v1/economics", {**w, "scope_type": "org", "scope_id": "org"}))
            for t in teams:
                _write(out_dir, f"team_{_slug(t['id'])}_{label}",
                      _get(client, f"/v1/teams/{t['id']}", w))
                _write(out_dir, f"economics_team_{_slug(t['id'])}_{label}",
                      _get(client, "/v1/economics", {**w, "scope_type": "team", "scope_id": t["id"]}))
            for p in projects:
                _write(out_dir, f"project_{_slug(p['id'])}_{label}",
                      _get(client, f"/v1/projects/{p['id']}", w))
                _write(out_dir, f"economics_project_{_slug(p['id'])}_{label}",
                      _get(client, "/v1/economics", {**w, "scope_type": "repo", "scope_id": p["id"]}))

        # The first 3 people, discovered from each team's own roster (no bare "list people"
        # manager endpoint exists -- U7's picker only enumerates teams/projects).
        person_ids: list[str] = []
        for t in teams:
            team_detail = _get(client, f"/v1/teams/{t['id']}", windows["30d"])
            if team_detail:
                person_ids.extend(p["user_id"] for p in team_detail["data"]["people"])
            if len(person_ids) >= 3:
                break
        person_ids = person_ids[:3]

        pr_id = feature_id = None
        for pid in person_ids:
            for label, w in windows.items():
                detail = _get(client, f"/v1/people/{pid}", w)
                _write(out_dir, f"person_{_slug(pid)}_{label}", detail)
                if detail and not pr_id and detail["data"]["recent_prs"]:
                    pr_id = detail["data"]["recent_prs"][0]["artifact_id"]

        for p in projects:
            detail = _get(client, f"/v1/projects/{p['id']}", windows["30d"])
            if detail and detail["data"]["features"]:
                feature_id = detail["data"]["features"][0]["id"]
                break

        if pr_id:
            _write(out_dir, f"pr_{_slug(pr_id)}", _get(client, f"/v1/prs/{pr_id}", {}))
        else:
            print("  ! no PR discovered via any sampled person's recent_prs", file=sys.stderr)

        if feature_id:
            _write(out_dir, f"feature_{_slug(feature_id)}", _get(client, f"/v1/features/{feature_id}", {}))
        else:
            print("  ! no feature discovered via any project's features[]", file=sys.stderr)

    written = sorted(p.name for p in out_dir.glob("*.json"))
    print(f"wrote {len(written)} snapshot(s) to {out_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:8090")
    parser.add_argument("--viewer", required=True, help="x-talium-viewer header value")
    parser.add_argument("--out", required=True, type=Path, help="snapshot output directory")
    parser.add_argument("--to", default=None,
                        help="ISO timestamp anchoring every window; pass the SAME value across "
                             "before/after runs so `diff -r` compares identical windows. "
                             "Defaults to now (UTC), which only works if runs happen instantly "
                             "after one another.")
    args = parser.parse_args()
    to = datetime.fromisoformat(args.to) if args.to else datetime.now(timezone.utc)
    snapshot(args.base_url, args.viewer, args.out, to)


if __name__ == "__main__":
    main()
