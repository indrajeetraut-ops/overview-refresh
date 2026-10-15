"""Layer 2: expand a completed series dataset into a multi-team org dataset (`org-expand`)."""
from __future__ import annotations

from .config import OrgConfig, ResolvedOrg, load_org_config, validate_org_inputs

__all__ = ["OrgConfig", "ResolvedOrg", "load_org_config", "validate_org_inputs", "run_org_expand"]


def run_org_expand(config: "OrgConfig", output_dir, *, log=None, run_id=None):
    """Generate the org dataset. Implemented in `expand.py` (task P8)."""
    from .expand import run_org_expand as execute
    return execute(config, output_dir, log=log, run_id=run_id)
