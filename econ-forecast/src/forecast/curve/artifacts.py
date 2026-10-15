"""Strict, verified reader for completed curve artifacts (Task 1 output).

Parallels `forecasting/artifacts.py::load_run`: never trust a curve artifact until its
manifest is read, its status is `complete`, and its `config.yaml` checksum and directory
identity match the manifest before any consumer (series generation) touches its anchors.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from forecast.artifact_io import sha256

from .config import load_curve_config
from .errors import ArtifactError, ConfigError
from .model import resolve_anchors

_DIGEST = re.compile(r"[0-9a-f]{64}")
_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*(?::[0-9a-f]{16})?")


@dataclass
class CurveArtifact:
    """A verified, loaded curve artifact ready for `evaluate_curve`."""
    name: str
    curve_id: str
    anchors: list[tuple[float, float]]
    axis: tuple[int, int]
    interp: str


def _is_complete(directory: Path) -> bool:
    """True only when `directory` holds a `manifest.json` recording a completed run."""
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return False
    return isinstance(manifest, dict) and manifest.get("status") == "complete"


def resolve_curve_ref(artifacts_root: Path, ref: str) -> Path:
    """Resolve a `name` or `name:id` reference to its artifact directory under `artifacts_root`.

    A bare `name` is allowed only when exactly one COMPLETE artifact exists under it
    (incomplete/orphaned sibling directories are ignored, not counted); otherwise the id is
    required. Raises `ArtifactError` for any unresolved, missing, or ambiguous reference.
    """
    if not isinstance(ref, str) or not _REF.fullmatch(ref):
        raise ArtifactError(f"invalid curve reference {ref!r}; expected name or name:id")
    name, _, curve_id = ref.partition(":")
    base = Path(artifacts_root) / name
    if curve_id:
        target = base / curve_id
        if not target.is_dir():
            raise ArtifactError(f"curve artifact not found: {ref!r} (looked in {target})")
        return target
    if not base.is_dir():
        raise ArtifactError(f"no curve artifacts named {name!r} under {artifacts_root}")
    complete = sorted(child for child in base.iterdir() if child.is_dir() and _is_complete(child))
    if not complete:
        raise ArtifactError(f"no complete curve artifact named {name!r} under {artifacts_root}")
    if len(complete) > 1:
        raise ArtifactError(
            f"ambiguous curve name {name!r}: {len(complete)} complete artifacts exist under "
            f"{base}; specify name:id")
    return complete[0]


def load_curve_artifact(dir_path: Path) -> CurveArtifact:
    """Verify and load a curve artifact directory, returning its resolved anchors.

    Verifies: the directory exists; `manifest.json` is present with `status == "complete"`;
    the `config.yaml` on disk hashes to the manifest's `config_sha256`; the directory's
    `<id>` component equals the short (16-hex) prefix of that hash; the directory's `<name>`
    component equals the manifest's recorded name. Only then are `config.yaml`'s anchors
    resolved via `resolve_anchors`. Raises `ArtifactError` on any violated invariant.
    """
    directory = Path(dir_path)
    if not directory.is_dir():
        raise ArtifactError(f"curve artifact directory not found: {directory}")
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ArtifactError(f"curve artifact has no manifest: {directory}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ArtifactError(f"cannot read curve manifest: {directory}") from exc
    if not isinstance(manifest, dict) or manifest.get("status") != "complete":
        raise ArtifactError(f"curve artifact is not complete: {directory}")
    digest, name = manifest.get("config_sha256"), manifest.get("name")
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise ArtifactError(f"curve manifest has an invalid config_sha256: {directory}")
    if not isinstance(name, str) or not name:
        raise ArtifactError(f"curve manifest has an invalid name: {directory}")
    config_path = directory / "config.yaml"
    if not config_path.is_file() or config_path.is_symlink():
        raise ArtifactError(f"curve artifact missing config.yaml: {directory}")
    try:
        actual_digest = sha256(config_path)
    except OSError as exc:
        raise ArtifactError(f"cannot read curve config: {directory}") from exc
    if actual_digest != digest:
        raise ArtifactError(f"curve config checksum mismatch (tampered or incomplete): {directory}")
    if directory.name != digest[:16]:
        raise ArtifactError(f"curve artifact directory id does not match its config hash: {directory}")
    if directory.parent.name != name:
        raise ArtifactError(f"curve artifact directory name does not match its manifest name: {directory}")
    try:
        config = load_curve_config(config_path)
    except ConfigError as exc:
        raise ArtifactError(f"curve config is invalid: {directory}: {exc}") from exc
    anchors = resolve_anchors(config.anchors, config.axis.start, config.axis.end)
    return CurveArtifact(name=name, curve_id=digest[:16], anchors=anchors,
                          axis=(config.axis.start, config.axis.end), interp=config.interp)
