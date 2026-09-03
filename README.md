# Socket to Ciphertext

An instant messaging system written in Python, from the raw TCP socket at the
bottom to end-to-end encrypted messages at the top, with a Tkinter interface
that lands last on purpose.

The server routes messages. By the end, it will not be able to read them.

Built as coursework, in nine phases, each one ending in something that runs.

---

## What works today

**Two people can hold a conversation.** Start the server, run a client in two
terminals, and you have a working messenger:

```
$ python -m im.client --user alice --password hunter2 --register

  logged in as alice. /help for commands.

[nobody] > /to bob

  -- now talking to bob --

[bob] > hello bob 你好 🔐

  you: hello bob 你好 🔐

[bob] >
  bob: hi alice, this is bob
```

Also working: several conversations at once with separate unread counts,
presence when someone logs in or drops, a clear error when you message someone
who is offline, and `/who`, `/chats` and `/history`.

What does **not** work yet: nothing is saved to disk, so accounts and history
vanish when the server restarts; nothing is encrypted; rooms route but cannot
be joined from a client; and there is no GUI. Those are phases 4 through 7.

| Phase | What it adds | State |
|-------|--------------|-------|
| 0 | Repo, package layout, toolchain | **done** |
| 1 | Sockets, framing, frozen protocol | **done** |
| 2 | Server core: registries, router, presence | **done** |
| 3 | Headless client: connection, model, console view | **done** |
| 4 | Rooms and concurrent conversations | next |
| 5 | Persistence, accounts, offline delivery | |
| 6 | TLS and end-to-end encryption | |
| 7 | Tkinter interface (design pass first) | |
| 8 | Internet demo, hardening, report | |

---

## Requirements

- **Python 3.11 or newer.** Enforced at import time — an older interpreter
  gets an explanation rather than a confusing `ImportError`.
- tkinter 8.6, bundled with the standard CPython installer on Windows and
  macOS. On Debian or Ubuntu: `sudo apt install python3-tk`. Not needed
  before phase 7.
- Everything else is in `requirements.txt`.

## Setup

```bash
git clone https://github.com/TurjoRahman-afk/socket-to-ciphertext.git
cd socket-to-ciphertext

python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS / Linux

pip install -r requirements.txt
```

## Running

```bash
python -m im.server                      # the hub, on 127.0.0.1:5000
python -m im.server --host 0.0.0.0       # reachable from other machines
python -m im.server --port 5050 --quiet
python -m im.client                      # a banner, until phase 3
```

Both accept `--help`. Stop the server with Ctrl-C; it closes its listening
socket and hangs up on everyone cleanly.

## Trying it by hand

There is no client yet, but the protocol is newline-delimited JSON, so any
tool that speaks TCP will do. Start the server, then in another terminal:

```bash
python - <<'PY'
import json, socket, time

def line(**f):
    return (json.dumps({"v": 1, **f}) + "\n").encode()

s = socket.create_connection(("127.0.0.1", 5000), timeout=5)
s.sendall(line(type="REGISTER", user="alice", pass_hash="pretend-digest"))
s.sendall(line(type="LOGIN",    user="alice", pass_hash="pretend-digest"))
time.sleep(0.3)
print(s.recv(65536).decode())
PY
```

Do the same in a third terminal as `bob`, then have alice send:

```python
s.sendall(line(type="MSG", to="bob", body="hello 你好 🔐"))
```

Bob's terminal receives the message with `"from": "alice"`, and alice gets an
`ACK`. `telnet 127.0.0.1 5000` works too — type prose and the server replies
with a `BAD_FRAME` error without hanging up on you.

---

## Architecture

### A hub, not peer-to-peer

Every client connects to one server, which routes between them. Offline
delivery, message history and presence all need somewhere central to live.
End-to-end encryption (phase 6) is what stops the hub reading what it routes,
so centralising delivery does not mean trusting the server with content.

### Threads, not asyncio

Chat is I/O-bound, so the GIL costs nothing here: a thread blocked in `recv()`
releases it. Threads are also a stated learning objective, and mixing an event
loop with Tkinter's own loop is genuinely awkward.

**Two threads per connection**, and one queue between them:

```
  client A                    SERVER                      client B
     |                                                       ^
     |  MSG                                                  |
     v                                                       |
  reader thread A ---> MessageRouter ---> outbox(B) ---> writer thread B
                       (pure logic,        queue.Queue
                        no sockets)        maxsize 1000
```

The router never touches a socket. It calls `send()`, which is `put_nowait`
and returns immediately, so **one client on a slow link cannot delay delivery
to anybody else**. The blocking write happens on a thread that connection owns
and nobody waits on.

Two rules hold the concurrency together:

- **The GIL is not a lock.** It makes a single dict operation atomic, but
  every registry operation is compound — *"is this name taken, and if not,
  claim it"* — so each one happens inside a single `threading.Lock` block.
- **Never send while holding a lock.** Registry methods that return a
  collection return a *copy*, so fan-out happens after the lock is released.
  Otherwise the slowest client sits on everyone else's critical path.

### The client is split so the interface can arrive last

```
  ServerConnection    socket thread, framing, connection state machine
        |
  ChatModel           conversations, roster, unread counts
        |             pure Python -- the word "tkinter" never appears here
  ChatController      gestures in, frames out; frames in, model updates out
        |
   +----+----+
 console    tk/       two interchangeable views over one model
 (phase 3)  (phase 7)
```

`im/client/model/` may never import `tkinter`. That is not a convention —
[tests/test_model_has_no_tkinter.py](tests/test_model_has_no_tkinter.py)
parses every file in the package and fails the build if it ever does. The rule
is what lets the console view and the Tk view be two views over one model, and
it is the evidence behind the MVC claim in the report.

When the GUI does arrive, worker threads and widgets meet in exactly one
place: the reader thread pushes decoded frames onto a `queue.Queue`, and the
main thread drains it in a `root.after(50, poll)` loop. Tkinter has no
`SwingUtilities.invokeLater`, and calling a widget from a worker thread
corrupts state silently rather than raising.

---

## Protocol

One UTF-8 line per frame, one JSON object per line, `\n` as the delimiter.
Debuggable over telnet, readable in a packet trace during a demo.

```json
{"v":1,"id":"7f3a...","type":"MSG","ts":1756684800000,
 "from":"alice","to":"#general",
 "body":"BASE64-CIPHERTEXT","n":"BASE64-NONCE"}
```

TCP is a byte stream with no message boundaries: one `recv()` may return half
a frame, or three frames, or a frame split mid-character. `LineBuffer` in
[im/common/codec.py](im/common/codec.py) is the only place in the project that
has to care.

Implemented so far: `REGISTER`, `LOGIN`, `MSG`, `PING`, and the server's
`OK`, `LOGIN_OK`, `ACK`, `PRESENCE`, `PONG`, `ERROR`. The remaining types are
declared and specified, and land in later phases.

The full specification, frozen at the end of phase 1, is in
[docs/protocol.md](docs/protocol.md). Changing anything in it needs agreement
from both tracks and a bump of `v`.

---

## Security

**Nothing is encrypted yet.** TLS and end-to-end encryption are phase 6. What
the server already does:

- passwords never travel or rest in plaintext — the client sends a digest,
  and the server compares it in constant time with `hmac.compare_digest`
- an unknown username and a wrong password produce the *same* error, so the
  server cannot be used to find out who has an account
- `from` is set by the server from the authenticated session, so a client
  cannot claim to be somebody else
- unauthenticated connections can do nothing but `PING`, `REGISTER`, `LOGIN`
- a client that never sends a newline is cut off at 1 MiB, and a client too
  slow to drain 1000 queued frames is disconnected rather than allowed to
  exhaust memory

Phase 6 adds an X25519 keypair per client, ECDH to HKDF to AES-GCM with a
fresh nonce per message, and TLS on the socket underneath. The server will
then hold ciphertext only.

What that will and will not protect — including metadata, forward secrecy and
key verification, none of which this design gives you — is written down
honestly in [docs/threat-model.md](docs/threat-model.md).

---

## Tests and tooling

```bash
pytest                  # 55 tests; a hung test fails after 30s
pytest --cov            # coverage, for the report
pytest tests/test_router.py   # routing rules, no sockets, instant
ruff check .            # lint
ruff format .           # format
```

The suite is split by what each part needs to run. `test_codec.py` and
`test_router.py` touch no network and finish in milliseconds;
`test_entrypoints.py` opens real sockets. When something breaks you know
immediately whether it is your logic or your networking.

`pytest-timeout` matters more here than in most projects: this is threads and
blocking sockets, where the natural failure mode is a deadlock rather than an
exception, and without a timeout a hung test blocks forever instead of
failing. It has already caught one.

---

## Layout

```
docs/          protocol spec, threat model, design report
im/common/     frames, codec, ids -- shared by both sides of the wire
im/crypto/     X25519 identity, AES-GCM envelope, TLS helpers   (phase 6)
im/server/     accept loop, per-client handler, router, registries, store
im/client/     net, model, controller, views                    (phase 3, 7)
tests/
```

`im/server/` and `im/client/` never import each other. They agree only
through `im/common/` and the protocol document.

## Design decisions

| Decision | Why |
|---|---|
| Threads, not asyncio | Stated objective; chat is I/O-bound; asyncio plus Tk's loop is awkward |
| Raw TCP, not WebSocket | Writing the framing *is* the exercise; a tunnel handles the internet demo |
| Hub, not peer-to-peer | Offline delivery, history and presence need a centre; E2EE keeps it blind |
| Newline-delimited JSON | Debuggable with telnet before any GUI exists |

## Known limitations

- Accounts live in memory and vanish when the server restarts (phase 5).
- A message to an offline user is refused rather than queued (phase 5).
- Rooms exist in the registry and messages fan out to their members, but no
  client command creates or joins one yet (phase 4).
- One username can only be connected once at a time.
- No rate limiting, and no cap on the number of accounts.

## Documentation

- [docs/protocol.md](docs/protocol.md) — the wire format, frozen after phase 1
- [docs/threat-model.md](docs/threat-model.md) — what the encryption will and will not protect
- [docs/design.md](docs/design.md) — the submitted report
