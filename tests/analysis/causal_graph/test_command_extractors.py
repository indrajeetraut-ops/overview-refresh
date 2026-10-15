from __future__ import annotations

import pytest

from analysis.causal_graph.command_extractors import CommandExtractorError, extract_commands


def test_bash_tree_sitter_preserves_source_order_and_duplicates():
    extracted = extract_commands(
        "bash_tree_sitter", "cd repo && git status | sed -n '1,20p' && git diff",
        basename_style="posix", command_case="preserve",
    )
    assert extracted.commands == ["cd", "git", "sed", "git"]
    assert extracted.warnings == []


def test_bash_extracts_unknown_executable_basename():
    extracted = extract_commands(
        "bash_tree_sitter", "/opt/acme/bin/my-linter --check .",
        basename_style="posix", command_case="preserve",
    )
    assert extracted.commands == ["my-linter"]


def test_malformed_bash_is_nonfatal_and_warned():
    extracted = extract_commands(
        "bash_tree_sitter", "git status && 'unterminated",
        basename_style="posix", command_case="preserve",
    )
    assert extracted.commands == []
    assert extracted.warnings[0]["code"] == "command_parse_failed"


def test_powershell_requires_its_own_runtime():
    with pytest.raises(CommandExtractorError, match="requires pwsh"):
        extract_commands(
            "powershell_ast", "Get-ChildItem . | Select-Object Name",
            basename_style="windows", command_case="casefold",
        )
