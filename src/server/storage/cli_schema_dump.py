"""Emit schema DDL and an ER diagram from Base.metadata, no DB connection (storage.md §7)."""
import argparse
import sys

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from .schema import Base


def dump_ddl() -> str:
    dialect = postgresql.dialect()
    stmts: list[str] = []

    for table in Base.metadata.sorted_tables:
        stmts.append(str(CreateTable(table).compile(dialect=dialect)).strip() + ";")
        for index in sorted(table.indexes, key=lambda ix: ix.name):
            stmts.append(str(CreateIndex(index).compile(dialect=dialect)).strip() + ";")

    return "\n\n".join(stmts) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cli_schema_dump")
    ap.add_argument("--ddl", action="store_true", help="emit CREATE TABLE DDL")
    ap.add_argument("--er", action="store_true", help="emit a Mermaid erDiagram")
    args = ap.parse_args(argv)
    if args.ddl:
        sys.stdout.write(dump_ddl())
    if args.er:
        from .er import dump_er  # provided in Task 6
        sys.stdout.write(dump_er())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
