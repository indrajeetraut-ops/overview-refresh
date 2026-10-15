"""Joint residual bootstrap, ETS simulation and nonnegative correction (design §5, §6)."""

import hashlib, json
import numpy as np


def joint_seed(seed, scope):
    raw = json.dumps(
        [seed, scope.org_id, scope.scope_type, scope.scope_id, "joint-errors-v1"],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return int.from_bytes(hashlib.sha256(raw).digest()[:16], "big")


def simulate_joint(items, internal_dates, paths, seed, tolerance):
    """Mutate fitted items with requested paths; returns sampling metadata per item.

    Correction-ratio-exceeded series stay `qualified` (they keep their corrected,
    nonnegative paths): the reader (`artifacts.py::load_run`) strictly derives scope
    status/risk_ready from series statuses, so introducing a rejection status here would
    make otherwise-valid runs unreadable. Instead `item["correction"]["passed"]` records
    the failure and the caller (runner.py) surfaces it as an `excess_negative_correction`
    assumption -- "unsuitable for budget risk but still reported" (design §6).
    """
    active = [x for x in items if x.get("fit") is not None]
    if not active:
        return
    common = set(active[0]["history"].dates)
    for x in active[1:]:
        common &= set(x["history"].dates)
    common = sorted(common)
    if len(common) < 14:
        for x in active:
            x["status"] = "sampling_failed"
            x["reasons"] = ["fewer than 14 shared residual dates"]
        return
    rng = np.random.default_rng(joint_seed(seed, active[0]["history"].scope))
    picks = rng.integers(0, len(common), size=(len(internal_dates), paths))
    for x in active:
        lookup = {d: r for d, r in zip(x["history"].dates, x["fit"].residuals)}
        residual = np.asarray([lookup[d] for d in common], dtype=np.float64)
        residual -= np.mean(residual)
        errors = residual[picks]
        try:
            raw = x["fit"].simulate(len(internal_dates), errors)
        except (ValueError, FloatingPointError, ArithmeticError) as exc:
            x["status"] = "sampling_failed"
            x["reasons"] = [f"ETS simulation failed: {type(exc).__name__}"]
            continue
        if not np.isfinite(raw).all():
            x["status"] = "sampling_failed"
            x["reasons"] = ["nonfinite ETS simulation"]
            continue
        requested_idx = x["requested_idx"]
        raw = raw[requested_idx]
        negative = raw < 0
        removed = float(np.maximum(-raw, 0).sum())
        corrected = np.maximum(raw, 0)
        total = float(corrected.sum())
        ratio = (
            None if total == 0 and removed > 0 else (removed / total if total else 0.0)
        )
        passed = removed == 0 if total == 0 else ratio <= tolerance
        x["sampling"] = {"start": common[0], "end": common[-1], "days": len(common)}
        x["correction"] = {
            "removed_tokens": removed,
            "corrected_tokens": total,
            "ratio": ratio,
            "negative_count": int(negative.sum()),
            "negative_fraction": float(negative.mean()),
            "tolerance": tolerance,
            "passed": passed,
        }
        x["paths"] = corrected
