"""OTLP intake: the receiver that makes the pipeline hybrid (design 2026-08-04-otel-intake).

OTel is spend truth, JSONL is session fidelity; they join on `request_id`. This package is
the OTel half -- a spec-compliant OTLP/HTTP receiver mounted inside the existing ingest
service (D-OT1), vendor-blind by construction: every vendor dialect lives in
`config/otel/{vendor}.yaml` and reaches this code only through `mapping.py`.
"""
