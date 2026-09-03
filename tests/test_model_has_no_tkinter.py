"""The architectural rule of this project, enforced as a test.

Nothing in ``im.client.model`` may import tkinter, any view, or the network
layer -- directly or transitively. That rule is what allows the console view
(phase 3) and the Tk view (phase 7) to be two interchangeable views over one
model, and what lets the model's tests run without a socket. It is the
evidence behind the MVC claim in the report.

It is easy to break by accident and cheap to check, so it is checked here.
"""

from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODEL_DIR = ROOT / "im" / "client" / "model"

#: An import is forbidden if its module name is one of these or starts with
#: one followed by a dot.
FORBIDDEN = (
    "tkinter",
    "im.client.view",
    "im.client.net",
    "im.server",
    "socket",
    "ssl",
)


def _imported_modules(source: str) -> set[str]:
    """Every module name imported, in full rather than just its root package."""
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
    return modules


def _is_forbidden(module: str) -> str | None:
    for banned in FORBIDDEN:
        if module == banned or module.startswith(banned + "."):
            return banned
    return None


def test_the_model_stays_independent_of_views_and_the_network() -> None:
    offenders = []
    for path in sorted(MODEL_DIR.rglob("*.py")):
        for module in sorted(_imported_modules(path.read_text(encoding="utf-8"))):
            banned = _is_forbidden(module)
            if banned is not None:
                offenders.append(f"{path.relative_to(ROOT)} imports {module}")

    assert not offenders, (
        "The model must stay independent of any view and of the transport.\n"
        "Move this into a view or the controller:\n  " + "\n  ".join(offenders)
    )


def test_the_guard_would_actually_catch_a_violation() -> None:
    """A test that never fails is not protecting anything."""
    assert _is_forbidden("tkinter") == "tkinter"
    assert _is_forbidden("tkinter.ttk") == "tkinter"
    assert _is_forbidden("im.client.view.console") == "im.client.view"
    assert _is_forbidden("im.client.net.connection") == "im.client.net"

    assert _is_forbidden("im.common.frames") is None
    assert _is_forbidden("im.client.model.events") is None
    assert _is_forbidden("dataclasses") is None
    # Not a prefix match on a bare string: "socketserver" is not "socket".
    assert _is_forbidden("socketserver") is None
