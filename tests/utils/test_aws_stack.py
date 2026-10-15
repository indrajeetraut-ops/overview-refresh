"""Invariants of the AWS team stack (docker/docker-compose.aws.yml + deploy/caddy/Caddyfile.aws).

Rendered through `docker compose config`, so interpolation is checked the way compose itself
does it; skipped where docker is not installed. The Caddyfile checks are plain text.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker" / "docker-compose.aws.yml"
LOCAL_OVERLAY = ROOT / "docker" / "docker-compose.aws.local.yml"
CADDYFILE = ROOT / "deploy" / "caddy" / "Caddyfile.aws"

ENV = {
    "POSTGRES_PASSWORD": "pw",
    "ECON_SITE": "econ.example.com",
    "SYNTH_SITE": "econ.example.com:9443",
    "SYNTH_PORT": "9443",
    "DEMO_USER": "demo",
    "DEMO_PASS_HASH": "$$2a$$14$$x",
    "TALIUM_ORG_ID": "talium",
    "TALIUM_S3_BUCKET": "talium-bucket",
    "TALIUM_S3_KEY_BASE": "upload/cc/logs/",
    "AWS_REGION": "us-west-2",
    "ECON_VIEWER_ID": "anil",
}


def _render(tmp_path: Path, *files: Path) -> dict:
    if shutil.which("docker") is None:
        pytest.skip("docker not installed")
    env_file = tmp_path / "aws.env"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in ENV.items()))
    cmd = ["docker", "compose", "--env-file", str(env_file)]
    for f in files:
        cmd += ["-f", str(f)]
    proc = subprocess.run(cmd + ["config", "--format", "json"], capture_output=True, text=True)
    if proc.returncode != 0 and "unknown flag" in proc.stderr:
        pytest.skip("docker compose too old for --format json")
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["services"]


def test_collector_sweeps_the_uploadd_prefix(tmp_path):
    svc = _render(tmp_path, COMPOSE)["collector"]
    assert svc["environment"]["COLLECT_SCOPE"] == "upload/cc/logs/uploads/"
    assert svc["environment"]["COLLECTOR_CONFIG"].endswith("collector.aws.yaml")
    # Instance role only: no static keys baked into the service.
    assert "AWS_ACCESS_KEY_ID" not in svc["environment"]


def test_synth_api_is_isolated_from_the_real_pipeline(tmp_path):
    services = _render(tmp_path, COMPOSE)
    synth = services["api_synth"]
    assert synth["environment"]["TALIUM_DB_DSN"].endswith("/econ_synth")
    # init builds the REAL schema; it must never gate (or be pointed at) the synthetic DB.
    assert "init" not in synth.get("depends_on", {})
    assert synth["build"]["args"]["VITE_VIEWER_ID"] == "github:vp-eng"
    # Nothing that folds or writes may ever see econ_synth.
    for name in ("init", "ingest", "worker", "collector", "api"):
        assert services[name]["environment"]["TALIUM_DB_DSN"].endswith("/econ"), name
    assert services["api"]["build"]["args"]["VITE_VIEWER_ID"] == "anil"


def test_only_caddy_is_published_and_no_uploader(tmp_path):
    services = _render(tmp_path, COMPOSE)
    assert "uploader" not in services
    published = {n for n, s in services.items() if s.get("ports")}
    assert published == {"caddy"}
    targets = {int(p["target"]) for p in services["caddy"]["ports"]}
    assert targets == {80, 443, 9443}
    # The rehearsal runs this file under another project name; fixed names would collide.
    assert not any("container_name" in s for s in services.values())


def test_local_overlay_only_adds_aws_credentials_to_the_collector(tmp_path):
    base = _render(tmp_path, COMPOSE)
    merged = _render(tmp_path, COMPOSE, LOCAL_OVERLAY)
    assert any(v["target"] == "/root/.aws" and v.get("read_only")
               for v in merged["collector"]["volumes"])
    for name in base:
        if name != "collector":
            assert merged[name].get("volumes") == base[name].get("volumes"), name


def test_caddyfile_routes():
    text = CADDYFILE.read_text()
    live = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    assert "{$ECON_SITE}" in live and "{$SYNTH_SITE}" in live
    assert "reverse_proxy api_synth:8090" in live
    # Uploads go to talium-uploadd on its own box; msging registration is open -- never routed.
    assert "/upload" not in live
    assert "/talium/ingest" not in live
    # /otlp/* is the one unauthenticated route, and only on the real site.
    assert live.count("handle /otlp/*") == 1
    assert live.index("handle /otlp/*") < live.index("{$SYNTH_SITE}")
