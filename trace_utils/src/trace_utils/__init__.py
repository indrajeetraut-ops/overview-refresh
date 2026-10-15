"""trace_utils — agent trace viewer for Hugging Face trace datasets.

Library core; the CLI (`cli_trace`) is a thin layer over these modules so a future
web/API layer can reuse the same functions. Dataset-native by design: nothing is
forced into a common schema (see docs/design/2026-07-13-trace-viewer-design.md).
"""

__version__ = "0.1.0"
