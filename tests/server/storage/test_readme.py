from pathlib import Path

README = Path("src/server/storage/README.md")


def test_readme_has_required_sections():
    text = README.read_text(encoding="utf-8")
    for section in ("## Public API", "## Quick example", "## Configuration",
                    "## DB management CLI", "## Running tests"):
        assert section in text, f"missing {section}"
    # config-driven DSN/user is documented
    assert "TALIUM_DB_DSN" in text
    assert "postgresql+psycopg://" in text
