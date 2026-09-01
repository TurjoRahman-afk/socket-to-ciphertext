"""The package's own preconditions."""

from __future__ import annotations

import importlib
import sys

import pytest

import im


def test_the_python_version_guard_fires_on_an_old_interpreter(monkeypatch) -> None:
    """A teammate on 3.10 must get an explanation, not an ImportError about
    StrEnum from somewhere inside the codec."""
    monkeypatch.setattr(sys, "version_info", (3, 10, 12, "final", 0))
    try:
        with pytest.raises(RuntimeError, match=r"needs Python 3\.11 or newer"):
            importlib.reload(im)
    finally:
        monkeypatch.undo()
        importlib.reload(im)


def test_the_guard_names_the_version_actually_running(monkeypatch) -> None:
    monkeypatch.setattr(sys, "version_info", (3, 9, 7, "final", 0))
    try:
        with pytest.raises(RuntimeError, match=r"but this is 3\.9\.7"):
            importlib.reload(im)
    finally:
        monkeypatch.undo()
        importlib.reload(im)


def test_the_package_imports_on_this_interpreter() -> None:
    assert im.MINIMUM_PYTHON == (3, 11)
    assert sys.version_info >= im.MINIMUM_PYTHON
