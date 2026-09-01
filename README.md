# Socket to Ciphertext

An instant messaging system in Python: raw TCP sockets at the bottom, end-to-end
encrypted messages at the top, and a Tkinter interface that lands last on purpose.

The server routes messages. It cannot read them.

## Status

| Phase | What it adds | State |
|-------|--------------|-------|
| 0 | Repo, package layout, toolchain | **done** |
| 1 | Sockets, framing, frozen protocol | **done** |
| 2 | Server core: registries, router, presence | not started |
| 3 | Headless client: connection, model, console view | not started |
| 4 | Rooms and concurrent conversations | not started |
| 5 | Persistence, accounts, offline delivery | not started |
| 6 | TLS and end-to-end encryption | not started |
| 7 | Tkinter interface (design pass first) | not started |
| 8 | Internet demo, hardening, report | not started |

## Requirements

- Python 3.11
- tkinter 8.6 (bundled with the standard CPython installer)
- Everything else in `requirements.txt`

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS / Linux
pip install -r requirements.txt
```

## Running

```bash
python -m im.server             # starts the hub
python -m im.client             # starts a client
```

Both accept `--help`. The server takes `--host` and `--port`; bind `0.0.0.0`
when you want to reach it from another machine on the same network.

## Tests and tooling

```bash
pytest                  # run the suite (hung tests fail after 30s)
pytest --cov            # with coverage, for the report
ruff check .            # lint
ruff format .           # format
```

Configuration lives in `pyproject.toml`. Everything the running system needs is
in the standard library except `cryptography`; the rest of `requirements.txt`
is development tooling.

## Layout

```
docs/       protocol spec, threat model, submitted design report
im/common/  frames, codec, ids -- shared by both sides of the wire
im/crypto/  X25519 identity, AES-GCM envelope, TLS context helpers
im/server/  accept loop, per-client handler, router, registries, sqlite store
im/client/  net (socket + state machine), model, controller, views
tests/      codec, router, crypto, integration
```

The client is split so that nothing above the socket knows Tkinter exists.
`im/client/model/` must never import `tkinter` -- that rule is what allows the
console view and the Tk view to be two interchangeable views over one model.

## Documentation

- `docs/protocol.md` -- wire format, frozen at the end of phase 1
- `docs/threat-model.md` -- what the encryption does and does not protect
- `docs/design.md` -- the submitted report
