"""Regenerate the annotated JSON blocks in the msging docs from `schema/*.json`.

`src/common/msging/schema/*.json` is canonical (see `models.py` and
`tests/common/msging/test_schema_parity.py`, which pins the pydantic models to
those files). The design doc and the module README quote the same shapes as
hand-typed JSON, which is exactly the kind of copy that drifts. This script
makes the copies derived: change a schema file, run this, and every block that
quotes it changes with it.

A managed block looks like this in Markdown -- the markers are HTML comments,
so they are invisible when rendered:

    <!-- gen:schema envelope -->
    ```json
    {
      "msg_id": 42,          // annotation, preserved across regeneration
      ...
    }
    ```
    <!-- /gen:schema -->

**Annotations live in the docs, not in the schema files.** `//` comments are
harvested from the block being replaced and re-attached to the keys that still
exist, so prose written by a human survives a shape change; a key that has been
removed takes its comment with it, and a new key arrives bare, waiting for one.
Keeping the comments out of the JSON is deliberate -- the schema files have to
stay parseable by `json.load`, which is what makes them canonical.

Usage:

    venv/bin/python utils/gen_schema_blocks.py            # rewrite in place
    venv/bin/python utils/gen_schema_blocks.py --check    # exit 1 if stale

Stdlib only, and it imports nothing from the project: it is a text tool.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = _ROOT / "src/common/msging/schema"

#: Every file that may contain managed blocks. A block names its schema, so one
#: file can carry several and the same schema can appear in several files.
TARGETS = (
    _ROOT / "docs/design/event-messaging-design.md",
    _ROOT / "src/common/msging/README.md",
)

#: `<!-- gen:schema NAME -->` ... fenced json ... `<!-- /gen:schema -->`
_BLOCK = re.compile(
    r"(?P<open><!--\s*gen:schema\s+(?P<name>[A-Za-z0-9_]+)\s*-->\n)"
    r"```json\n(?P<body>.*?)```\n"
    r"(?P<close><!--\s*/gen:schema\s*-->)",
    re.DOTALL,
)

#: Where the `//` annotations start, in columns. Wide enough for the longest
#: value in these schemas; a longer one just pushes its own comment right.
_COMMENT_COL = 44


def _strip_comment(line: str) -> tuple[str, str]:
    """Split a rendered line into (code, comment) at the first `//` that is
    outside a JSON string. Naive splitting breaks on `https://`, which is a
    plausible value in any of these blocks."""
    in_string = False
    escaped = False
    for i, ch in enumerate(line):
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if not in_string and ch == "/" and line[i:i + 2] == "//":
            return line[:i].rstrip(), line[i + 2:].strip()
    return line.rstrip(), ""


def harvest(body: str) -> dict[str, str]:
    """`key -> annotation` from a previously rendered block."""
    out: dict[str, str] = {}
    for line in body.splitlines():
        code, comment = _strip_comment(line)
        if not comment:
            continue
        match = re.match(r'\s*"(?P<key>[^"]+)"\s*:', code)
        if match:
            out[match.group("key")] = comment
    return out


def render(data: dict, comments: dict[str, str]) -> str:
    """One managed block's JSON body: aligned keys, aligned `//` annotations.

    Top-level keys only -- these schemas are flat by design (`info` and
    `payload` are deliberately unschema'd, §7), and a nested renderer would be
    inventing a formatting policy for shapes that do not exist.
    """
    keys = list(data)
    if not keys:
        return "{}\n"
    key_width = max(len(k) for k in keys) + 3          # quotes + colon
    lines = ["{"]
    for i, key in enumerate(keys):
        value = json.dumps(data[key], ensure_ascii=False)
        tail = "," if i < len(keys) - 1 else ""
        code = f'  {(chr(34) + key + chr(34) + ":"):<{key_width}} {value}{tail}'
        comment = comments.get(key)
        if comment:
            code = f"{code:<{_COMMENT_COL}} // {comment}"
        lines.append(code.rstrip())
    lines.append("}")
    return "\n".join(lines) + "\n"


def load_schema(name: str) -> dict:
    path = SCHEMA_DIR / f"{name}.json"
    if not path.exists():
        raise SystemExit(f"no such schema: {path}")
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def process(path: Path) -> tuple[str, int]:
    """Return the file's new text and how many managed blocks it has."""
    text = path.read_text(encoding="utf-8")
    count = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        body = render(load_schema(match.group("name")),
                      harvest(match.group("body")))
        return f"{match.group('open')}```json\n{body}```\n{match.group('close')}"

    return _BLOCK.sub(_replace, text), count


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="do not write; exit 1 if any block is out of date")
    args = ap.parse_args(argv)

    stale = 0
    for path in TARGETS:
        if not path.exists():
            print(f"skipping missing {path}", file=sys.stderr)
            continue
        old = path.read_text(encoding="utf-8")
        new, blocks = process(path)
        rel = path.relative_to(_ROOT)
        if not blocks:
            print(f"{rel}: no gen:schema blocks")
            continue
        if new == old:
            print(f"{rel}: {blocks} block(s), up to date")
            continue
        stale += 1
        if args.check:
            print(f"{rel}: {blocks} block(s), OUT OF DATE", file=sys.stderr)
        else:
            path.write_text(new, encoding="utf-8")
            print(f"{rel}: {blocks} block(s), rewritten")
    if args.check and stale:
        print("run `venv/bin/python utils/gen_schema_blocks.py` to update",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
