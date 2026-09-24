"""Work out why the window will not open on this machine.

    python -m tools.doctor

The console client working while the window does not is almost always one of
a small number of things, and none of them announce themselves: the
application is launched with pythonw.exe, which has no console, so anything
that goes wrong at startup has nowhere to print. The window simply never
appears.

This checks each cause in turn and says what to do about the one that failed.
Run it with python.exe, not pythonw.exe -- the whole point is to see output.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PASS = "  [ ok ] "
FAIL = "  [FAIL] "
WARN = "  [warn] "

problems: list[str] = []


def report(ok: bool, title: str, detail: str = "", fix: str = "") -> bool:
    print(f"{PASS if ok else FAIL}{title}")
    if detail:
        print(f"         {detail}")
    if not ok and fix:
        # The fix text is written indented to line up under the check above
        # it. Flatten it here so the summary at the end can indent it its own
        # way rather than inheriting two different amounts.
        flat = "\n".join(line.strip() if line.strip() else "" for line in fix.split("\n"))
        problems.append((title, flat))
    return ok


def check_python() -> bool:
    version = ".".join(str(n) for n in sys.version_info[:3])
    ok = sys.version_info >= (3, 11)
    return report(
        ok,
        f"Python {version}",
        f"running from {sys.executable}",
        "Semaphore needs Python 3.11 or newer -- it uses enum.StrEnum, which\n"
        "         arrived in 3.11. Install a newer Python and rebuild the venv:\n\n"
        "             python -m venv .venv\n"
        "             .venv\\Scripts\\activate\n"
        "             pip install -r requirements.txt",
    )


def check_tkinter() -> bool:
    try:
        import tkinter  # noqa: F401
    except ImportError as exc:
        return report(
            False,
            "tkinter is importable",
            str(exc),
            "THIS IS THE USUAL CAUSE. Python is installed without Tcl/Tk, so the\n"
            "         console client works and the window cannot exist.\n\n"
            "         Windows: re-run the Python installer, choose Modify, and tick\n"
            "                  'tcl/tk and IDLE'. Then delete .venv and make it again.\n"
            "         Linux:   sudo apt install python3-tk\n"
            "         macOS:   brew install python-tk\n\n"
            "         Python from the Microsoft Store often omits it. A python.org\n"
            "         install is the reliable one.",
        )
    return report(True, "tkinter is importable")


def check_tk_window() -> bool:
    """Importing tkinter is not the same as being able to open a window."""
    try:
        import tkinter as tk

        root = tk.Tk()
        version = root.tk.call("info", "patchlevel")
        root.destroy()
    except Exception as exc:  # noqa: BLE001 -- any failure here is worth reporting
        return report(
            False,
            "Tk can open a window",
            f"{type(exc).__name__}: {exc}",
            "tkinter imports but cannot create a window. On Windows this usually\n"
            "         means a broken or partial Tcl/Tk installation -- reinstall Python\n"
            "         with the tcl/tk option. Over SSH or in a container it means there\n"
            "         is no display, and the console view is the only option:\n\n"
            "             python -m im.client --view console --user you --password pw",
        )
    return report(True, f"Tk can open a window (Tk {version})")


def check_pythonw() -> bool:
    """run.bat launches with pythonw.exe and never checks that it exists."""
    if os.name != "nt":
        return report(True, "pythonw not needed on this platform")

    venv = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    beside = Path(sys.executable).with_name("pythonw.exe")
    found = venv.exists() or beside.exists()
    return report(
        found,
        "pythonw.exe exists",
        str(venv if venv.exists() else beside) if found else "not beside python.exe or in .venv",
        "run.bat launches the window with pythonw.exe so no console sits behind\n"
        "         it. Without that file nothing starts, and because pythonw has no\n"
        "         console there is no error either.\n\n"
        "         Run the client directly instead, which works and shows errors:\n\n"
        "             .venv\\Scripts\\python.exe -m im.client --view tk --user aya \\\n"
        "                 --password demo --register",
    )


def check_venv() -> bool:
    if os.name != "nt":
        return report(True, "virtual environment (not checked off Windows)")
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    return report(
        python.exists(),
        "virtual environment exists",
        str(python) if python.exists() else "no .venv in the project directory",
        "Make one in the project directory:\n\n"
        "             python -m venv .venv\n"
        "             .venv\\Scripts\\activate\n"
        "             pip install -r requirements.txt",
    )


def check_dependencies() -> bool:
    missing = []
    for module in ("cryptography",):
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(module)
    return report(
        not missing,
        "dependencies installed",
        "cryptography" if not missing else f"missing: {', '.join(missing)}",
        "             pip install -r requirements.txt",
    )


def check_backdrop() -> bool:
    """The generated background needs Tk to accept a PPM from memory."""
    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        try:
            data = b"P6\n2 2\n255\n" + bytes([255, 0, 0] * 4)
            tk.PhotoImage(data=data, format="ppm")
        finally:
            root.destroy()
    except Exception as exc:  # noqa: BLE001
        return report(
            False,
            "Tk can load the generated background",
            f"{type(exc).__name__}: {exc}",
            "The soft background is built in memory as a PPM. This Tk will not\n"
            "         read one, which is unusual. The window still works without it --\n"
            "         the backdrop is decoration -- so this is a warning, not a stop.",
        )
    return report(True, "Tk can load the generated background")


def check_app_imports() -> bool:
    try:
        importlib.import_module("im.client.view.tk")
    except Exception as exc:  # noqa: BLE001
        return report(
            False,
            "the window code imports",
            f"{type(exc).__name__}: {exc}",
            "Something in the application itself fails to import. The message\n"
            "         above is the real error -- the one pythonw would have swallowed.",
        )
    return report(True, "the window code imports")


def main() -> int:
    print()
    print("  Semaphore -- why will the window not open?")
    print("  " + "-" * 56)
    print()

    check_python()
    check_venv()
    check_dependencies()
    tk_ok = check_tkinter()
    if tk_ok:
        tk_ok = check_tk_window()
    check_pythonw()
    if tk_ok:
        check_backdrop()
        check_app_imports()

    print()
    if not problems:
        print("  Everything needed for the window is present.")
        print()
        print("  If it still will not open, run the client directly so that errors")
        print("  are visible instead of being swallowed by pythonw:")
        print()
        print("      python -m im.client --view tk --user aya --password demo --register")
        print()
        return 0

    print("  " + "=" * 56)
    print(f"  {len(problems)} problem(s) found")
    print("  " + "=" * 56)
    for i, (title, fix) in enumerate(problems, 1):
        print(f"\n  {i}. {title}\n")
        for line in fix.split("\n"):
            print(f"     {line}" if line else "")
    print()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
