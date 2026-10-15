"""Configured shell-command extraction for causal-graph construction."""
from __future__ import annotations

import ntpath
import os.path
from dataclasses import dataclass
from typing import Callable

from tree_sitter import Language, Parser
import tree_sitter_bash

__all__ = ["CommandExtraction", "CommandExtractorError", "extract_commands"]


class CommandExtractorError(ValueError):
    """A configured command parser could not run on the build host."""


@dataclass(frozen=True)
class CommandExtraction:
    commands: list[str]
    warnings: list[dict]


def _normalize_basename(value: str, *, style: str, case: str) -> str:
    basename = (ntpath.basename(value) if style == "windows" else os.path.basename(value)).strip("'\"")
    if style == "windows" and basename.casefold().endswith((".exe", ".cmd", ".bat", ".ps1")):
        basename = basename.rsplit(".", 1)[0]
    return basename.casefold() if case == "casefold" else basename


def _bash_tree_sitter(command: str) -> tuple[list[str], list[dict]]:
    parser = Parser(Language(tree_sitter_bash.language()))
    source = command.encode("utf-8")
    tree = parser.parse(source)
    if tree.root_node.has_error:
        return [], [{"code": "command_parse_failed", "message": "Bash parser rejected command text"}]
    commands: list[str] = []

    def visit(node) -> None:
        if node.type == "command":
            name = node.child_by_field_name("name")
            if name is not None:
                commands.append(source[name.start_byte:name.end_byte].decode("utf-8"))
        for child in node.children:
            visit(child)

    visit(tree.root_node)
    return commands, []


def _powershell_ast(command: str) -> tuple[list[str], list[dict]]:
    """Reserved adapter boundary; a PowerShell-capable host is required for its own AST."""
    raise CommandExtractorError(
        "powershell_ast requires pwsh on the graph-build host; no token-splitting fallback exists"
    )


_ADAPTERS: dict[str, Callable[[str], tuple[list[str], list[dict]]]] = {
    "bash_tree_sitter": _bash_tree_sitter,
    "powershell_ast": _powershell_ast,
}


def extract_commands(
    parser_name: str,
    command_text: str,
    *,
    basename_style: str,
    command_case: str,
) -> CommandExtraction:
    """Extract ordered executable basenames without filtering unknown commands."""
    adapter = _ADAPTERS.get(parser_name)
    if adapter is None:
        raise CommandExtractorError(f"unknown command extractor {parser_name!r}")
    if not isinstance(command_text, str):
        return CommandExtraction([], [{"code": "command_input_missing", "message": "configured command field is not text"}])
    raw, warnings = adapter(command_text)
    return CommandExtraction(
        [_normalize_basename(value, style=basename_style, case=command_case) for value in raw if value],
        warnings,
    )
