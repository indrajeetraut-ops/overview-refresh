"""The two halves must not import each other, and msging must import without
SQLAlchemy installed. Both are load-bearing design constraints, not style."""
import ast
import pathlib

MSGING = pathlib.Path(__file__).resolve().parents[3] / "src" / "common" / "msging"

# Fail loudly at collection time if the package moved/vanished, instead of
# letting every test below pass vacuously over an empty rglob().
assert MSGING.is_dir(), f"expected msging package at {MSGING}"


def _imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                # `from ..srv import x` / `from common.msging.srv import x`
                names.add(node.module)
                names.update(f"{node.module}.{a.name}" for a in node.names)
            else:
                # `from . import srv` / `from .. import srv` -- relative
                # imports with no module put the referenced name in
                # node.names instead, so node.module alone misses it.
                names.update(a.name for a in node.names)
    return names


def test_clnt_never_imports_srv():
    clnt = MSGING / "clnt"
    assert clnt.is_dir(), f"expected {clnt} to exist"
    paths = list(clnt.rglob("*.py"))
    assert paths, f"no python files found under {clnt}"
    for path in paths:
        for name in _imports(path):
            assert "srv" not in name.split("."), f"{path.name} imports {name}"


def test_srv_never_imports_clnt():
    srv = MSGING / "srv"
    assert srv.is_dir(), f"expected {srv} to exist"
    paths = list(srv.rglob("*.py"))
    assert paths, f"no python files found under {srv}"
    for path in paths:
        for name in _imports(path):
            assert "clnt" not in name.split("."), f"{path.name} imports {name}"


def test_no_toplevel_sqlalchemy_anywhere():
    """The postgres adapter is lazy-imported inside make_store (design §5.2)."""
    paths = list(MSGING.rglob("*.py"))
    assert paths, f"no python files found under {MSGING}"
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:            # module level only
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                text = ast.dump(node)
                assert "sqlalchemy" not in text.lower(), f"{path.name}"
                assert "psycopg" not in text.lower(), f"{path.name}"
