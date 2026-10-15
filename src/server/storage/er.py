"""Mermaid erDiagram generated from Base.metadata (storage.md §7)."""
from .schema import Base


def dump_er() -> str:
    lines = ["erDiagram"]
    for table in Base.metadata.sorted_tables:
        lines.append(f"    {table.name} {{")
        for col in table.columns:
            lines.append(f"        {col.type.__class__.__name__} {col.name}")
        lines.append("    }")
    for table in Base.metadata.sorted_tables:
        for fk in table.foreign_keys:
            parent = fk.column.table.name
            lines.append(f"    {parent} ||--o{{ {table.name} : has")
    return "\n".join(lines) + "\n"
