"""The architectural rule of this project, enforced as a test.

Nothing in ``im.client.model`` may import tkinter, directly or transitively.
That rule is what allows the console view (phase 3) and the Tk view (phase 7)
to be two interchangeable views over one model -- and it is the evidence for
the MVC claim in the report. It is easy to break by accident and cheap to
check, so it is checked here from phase 0 onward.
"""

from __future__ import annotations

import ast
import pathlib

MODEL_DIR = pathlib.Path(__file__).resolve().parent.parent / "im" / "client" / "model"

FORBIDDEN = {"tkinter"}


def _imported_roots(source: str) -> set[str]:
    """Top-level package name of every import in a module."""
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_model_package_never_imports_tkinter() -> None:
    offenders = []
    for path in sorted(MODEL_DIR.rglob("*.py")):
        roots = _imported_roots(path.read_text(encoding="utf-8"))
        for bad in sorted(roots & FORBIDDEN):
            offenders.append(f"{path.relative_to(MODEL_DIR.parent.parent.parent)} imports {bad}")

    assert not offenders, (
        "The model must stay view-independent. Move this into a view package:\n  "
        + "\n  ".join(offenders)
    )
