"""Socket to Ciphertext -- an end-to-end encrypted instant messaging system.

Layout:
    im.common   frames, codec, ids -- shared by both sides of the wire
    im.crypto   X25519 identity, AES-GCM envelope, TLS context helpers
    im.server   accept loop, handlers, router, registries, store
    im.client   connection, model, controller, views
"""

import sys

__version__ = "0.1.0"

#: Minimum interpreter. im/common/frames.py uses enum.StrEnum, which landed in
#: 3.11. requirements.txt cannot express this, so it is checked here -- on an
#: older interpreter the failure would otherwise be an ImportError deep in the
#: codec, which tells a teammate nothing useful.
MINIMUM_PYTHON = (3, 11)

if sys.version_info < MINIMUM_PYTHON:  # pragma: no cover -- depends on the interpreter
    required = ".".join(map(str, MINIMUM_PYTHON))
    running = ".".join(map(str, sys.version_info[:3]))
    raise RuntimeError(
        f"Socket to Ciphertext needs Python {required} or newer, but this is "
        f"{running}. Rebuild the virtual environment with a newer interpreter: "
        f"py -{required} -m venv .venv"
    )
