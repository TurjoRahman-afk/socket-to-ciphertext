# Semaphore -- Design Report

**Course:** Computer Software and Big Data Development
**Repository:** `socket-to-ciphertext` -- named for the build arc, from the raw
TCP socket at one end to end-to-end encryption at the other.

| Student ID | Name |
|------------|------|
| 1820242075 | Aiuna |
| 1820242085 | Faiza |
| 1820242086 | Turjo |
| 1820242087 | Aya |
| 1820242109 | Keisha |

> **To the team:** sections marked **[write this yourselves]** need your own
> words -- who did what, and what you would do differently. Everything else is
> an accurate account of the code as it stands, with measurements taken on the
> machine named in §8. Please read it before submitting rather than trusting it.

---

## 1. Requirements

| Requirement | Where it is satisfied | Status |
|---|---|---|
| Client/server over TCP sockets | `im/server/server.py`, `im/client/net/connection.py` | done |
| Multiple simultaneous clients | thread-per-connection, `im/server/handler.py` | done, measured to 374 |
| Application-level protocol | `im/common/frames.py`, `im/common/codec.py` | done, 21 frame types |
| User accounts and authentication | `im/server/store/users.py` (scrypt) | done |
| Direct messaging | `MessageRouter._message` | done |
| Group chat / rooms | `RoomRegistry`, `CREATE_ROOM` / `JOIN` / `INVITE` | done |
| Presence (online / offline) | `PRESENCE` frames, `SessionRegistry` | done |
| Typing indicators | `TYPING` frames, relayed not stored | done |
| Message persistence and history | `im/server/store/messages.py` (sqlite) | done |
| Offline delivery | queued per user, flushed on login | done |
| Delivery and read receipts | `RECEIPT` frames, three states | done, direct messages only |
| Graphical user interface | `im/client/view/tk/` (Tkinter) | done |
| Reconnection | `im/client/net/session.py`, exponential backoff | done |
| Encryption | X25519 + HKDF + AES-GCM; TLS optional | done, direct messages and rooms |
| Message search | `ChatModel.search`, client-side | done -- see 6.5 |

Room encryption was the last gap to close. The order it happened in -- claimed,
corrected to admit it was false, then made true -- is recorded in §9.3 rather
than smoothed over.

---

## 2. Architecture

### 2.1 Topology

A **hub**, not a peer-to-peer mesh. Every client holds one TCP connection to
one server; the server routes. This was chosen because the brief asks for
sockets and concurrency, and a hub puts both in one place where they can be
reasoned about and tested. A mesh would have meant NAT traversal, which is a
networking problem rather than a systems one, and would have hidden the
concurrency behind it.

```
  client ─┐
  client ─┼─► server ──► sqlite
  client ─┘   (routes,   (accounts, history,
              persists)   offline queue)
```

### 2.2 The client, split four ways

The client is deliberately in four layers, each of which knows only the one
below it:

| Layer | Knows about | Does not know about |
|---|---|---|
| `net/` | sockets, TLS, frames, threads | conversations, widgets |
| `model/` | conversations, roster, unread counts | frames, sockets, widgets |
| `controller/` | frames **and** the model -- the translation layer | sockets, widgets |
| `view/` | widgets and the model | frames, sockets |

The point of the split is that the view arrives last. For most of the project
there was no window at all, only `view/console.py`, and the model and
controller were fully exercised before a single widget existed. That the two
views are interchangeable over one model is the architectural claim, and it is
checked by a test rather than asserted (§8).

`tests/test_model_has_no_tkinter.py` enforces the boundary mechanically: it
fails if the model ever imports a GUI library. A comment saying "keep these
separate" decays; a test does not.

### 2.3 The server, split three ways

`MessageRouter` is **pure logic**. It imports no `socket`, no `ssl` and no
`threading`, and reaches a connection only through a `Session` protocol whose
`send()` drops a frame on a queue. Every routing rule is therefore testable
with a fake session that appends to a list -- no network, no port to bind.
That is why `test_router.py` holds 74 tests and runs instantly.

The consequence is that the server can be wrong about sockets, or wrong about
routing, but never confusingly wrong about both at once.

---

## 3. Protocol

### 3.1 Framing

Newline-delimited JSON. One frame per line, UTF-8.

```json
{"type":"MSG","id":"8f3c…","from":"turjo","to":"aya","body":"…","nonce":"…","ts":1758…}
```

**Why newline-delimited JSON over the alternatives:**

| Option | Why not |
|---|---|
| Fixed-width binary | Fast, but every field change is a rewrite, and a malformed frame is unreadable in a packet capture. |
| Length-prefixed binary | Correct and compact, but you cannot read the stream with `telnet`, which cost real debugging time during development. |
| Raw JSON, no delimiter | No way to know where one object ends without a streaming parser. |
| **Newline-delimited JSON** | **Self-describing, human-readable during debugging, one obvious frame boundary.** |

The cost is size and parse time, which the measurements in §8 show is not the
bottleneck at this scale.

### 3.2 The part that is easy to get wrong

TCP is a **byte stream, not a message stream**. One `recv()` may return half a
frame, three frames, or a frame split in the middle of a multi-byte UTF-8
character. `LineBuffer` in `im/common/codec.py` exists solely for this, and
`test_codec.py` spends 25 tests on it, including deliberately splitting a
multi-byte character across two reads.

This is the single most common beginner error in socket programming and the
reason the codec is its own module with its own test file.

### 3.3 Frame types

21 in total: `REGISTER`, `LOGIN`, `GET_KEY`, `MSG`, `CREATE_ROOM`, `JOIN`,
`LEAVE`, `INVITE`, `TYPING`, `RECEIPT`, `HISTORY`, `PING` client-to-server;
`OK`, `ERROR`, `LOGIN_OK`, `KEY`, `ACK`, `ROOM_STATE`, `HISTORY_RESULT`,
`PONG`, `PRESENCE` server-to-client. Full payloads in `docs/protocol.md`.

---

## 4. Threading model

### 4.1 The shape

**Thread-per-connection**, with a bounded queue handing off to a writer:

```
   socket ──► reader thread ──► router ──► recipient's outbox queue
                                                      │
   socket ◄── writer thread ◄──────────────────────────┘
```

Each connection costs two threads on the server and two on the client. Only
the reader calls `recv()`; only the writer calls `sendall()`. Nothing else
touches the socket. This is the rule the whole design rests on.

**Why a queue between them:** sending never blocks the caller. A slow or
stalled recipient cannot freeze the sender, and on the client it cannot freeze
the Tkinter main loop.

**Why bounded:** an unbounded queue turns a slow consumer into memory
exhaustion. `OUTBOX_LIMIT` is 100 frames, and a full outbox is given
`OUTBOX_WAIT` (0.5s) to drain before the connection is declared stalled --
because a full queue can mean a broken link *or* simply that the writer thread
has not been scheduled yet. An earlier version lacked that wait and tore down
connections at exactly 100 queued messages; the throughput benchmark in §8 is
what found it.

### 4.2 The GIL is not a lock

This was the concurrency misconception the project had to get past. The GIL
guarantees that one bytecode instruction does not interleave; it guarantees
nothing about a sequence of them. This is a race:

```python
if username not in self.sessions:      # thread A checks
    self.sessions[username] = session  # thread B has already written
```

Every compound check-then-write goes under a `threading.Lock`. `SessionRegistry`
and `RoomRegistry` do this, and their read methods return **copies**, so a
caller cannot iterate a registry while another thread mutates it.

### 4.3 The rule about locks and sockets

**Never send while holding a lock.** Fan-out to a room collects the member
sessions under the lock, releases it, and only then sends. Sending inside the
lock would mean one unresponsive client's blocked `sendall()` holding the
registry closed against every other thread on the server -- one slow client
freezing everybody.

### 4.4 Tkinter has no `invokeLater`

Tk is not thread-safe, and unlike Swing it offers no official way to schedule
work onto its event thread. Touching a widget from a worker thread corrupts Tk
*quietly* rather than raising, which is worse than a crash.

The bridge is three lines of concept:

```
reader thread ──► post_frame() ──► queue.Queue ──► root.after(50, _poll) ──► widgets
```

The reader thread only ever puts on the queue. The main thread drains it every
50ms. No widget is ever touched from anywhere else.

---

## 5. Connection state machine

The client's connection is an explicit FSM rather than a set of booleans,
because "connected" turned out to mean at least four different things.

```
  DISCONNECTED ──connect──► CONNECTING ──socket──► AUTHENTICATING
                                 │                        │
                              failed                   login ok
                                 ▼                        ▼
                              CLOSED ◄──close────────── ONLINE
                                 ▲                        │
                                 └──── RETRYING ◄──lost───┘
```

Sending is permitted in exactly one state, `ONLINE`, and the composer in the
window is enabled from that same fact -- so the interface cannot offer an
action the connection would reject.

`Session` wraps `ServerConnection` and replaces it on reconnect, with
exponential backoff plus jitter (jitter so that a server coming back up is not
hit by every client at the same instant). A heartbeat `PING` every 15 seconds
detects the dead-but-open socket, which TCP will otherwise not report for a
very long time; two missed replies rather than one, so a single dropped packet
does not tear down a live connection.

**A bug worth recording.** `Session` forwards its sending surface to the
current connection *by hand*, and that list silently fell behind: `receipt()`,
`invite()` and the members argument to `create_room()` were missing. Because
the app launches with `pythonw` (no console) and Tk writes callback exceptions
to stderr, the failures were completely invisible -- buttons that appeared
dead, and read receipts that had never once been sent. There is now a test
comparing the two classes method by method and signature by signature, and
`TkView` installs `report_callback_exception` so nothing is swallowed again.

---

## 6. Security

### 6.1 Passwords

The client hashes before sending, and the server derives again with **scrypt**
and a per-user random salt; comparison uses `hmac.compare_digest`. Unknown
user and wrong password return the *same* error, so the login path cannot be
used to enumerate accounts.

### 6.2 End-to-end encryption

X25519 key agreement → HKDF → AES-GCM with a fresh nonce per message. The
private key never leaves the client that generated it. The sender's name is
authenticated alongside the ciphertext, so the server cannot relabel a message
without the recipient's decryption failing.

### 6.3 TLS

Separate and independent. TLS protects framing and metadata from third parties
on the network; it does **not** protect anything from the server, because that
is where it terminates. It is enabled with `--tls` and is **off by default**,
including in `run.bat`.

### 6.4 Rooms

A frame carries one body and a room has one key per member, so a room message
is sealed once per member and the ciphertexts travel beside the frame in
`data["env"]`. The server hands each member their own envelope and stores the
whole map in one row; it holds no key for any of them.

Three properties fall out of that, and all three are consequences rather than
choices:

- A fresh nonce per member as well as per message. Two members share no key,
  and it is that pairing which makes nonce reuse catastrophic.
- Sealing is all or nothing. Encrypting only to the members whose keys we
  hold would drop the rest out of the conversation silently.
- Someone added later cannot read what came before. No envelope was sealed
  for them. That is what end-to-end encryption means, not a delivery failure.

One ciphertext per member per message is fine for five and wrong for five
hundred, which would want a shared group key rotated on membership change.

### 6.5 What this does not protect -- stated plainly

- **Metadata.** The server knows who talks to whom and when, because it
  routes. With every body sealed, this is the largest remaining exposure.
- **Server-side search is impossible**, not merely unimplemented. Searching
  what the server stores would mean handing it the keys. Search therefore
  runs client-side over what has been decrypted, and a miss says so.
- No forward secrecy; no key verification; no at-rest protection of the local
  key file; TLS off unless asked for.

`docs/threat-model.md` carries the full table. That file was wrong once -- it
claimed per-member room encryption before it existed -- and its header keeps
the sequence rather than tidying it away.

---

## 7. Persistence

sqlite, one connection behind a lock, in WAL mode. Three concerns:

| Concern | Behaviour |
|---|---|
| Accounts | username, salt, scrypt digest, public key |
| History | every message, retrievable per conversation |
| Offline queue | messages for absent users, flushed on their next login |

Ordering is `ORDER BY ts DESC, rowid DESC`. The tiebreak matters: an earlier
version broke ties on a random UUID, so two messages written in the same
millisecond came back in a *different order on every read*.

---

## 8. Testing and measurement

### 8.1 Strategy

Two kinds of test, deliberately separated:

- **Unit tests without sockets.** The router, model, controller, codec and
  crypto are tested with fakes. No port is bound, so they are fast,
  deterministic, and cannot fail because of a busy machine.
- **Integration tests with sockets.** A real server on an ephemeral port, real
  clients, real reconnection.

**316 tests**, all passing.

| Count | File | Covers |
|------:|------|--------|
| 74 | `test_router.py` | every routing rule, no network |
| 40 | `test_model.py` | conversations, unread, presence, search |
| 31 | `test_controller.py` | frames ↔ model translation, decryption |
| 30 | `test_store.py` | sqlite, scrypt, history ordering |
| 30 | `test_crypto.py` | key agreement, sealing per member, tampering |
| 25 | `test_codec.py` | framing, split reads, multi-byte splits |
| 20 | `test_tk_view.py` | the window, the room dialog, search |
| 19 | `test_connection_state.py` | the FSM |
| 19 | `test_connection.py` | client connection behaviour |
| 16 | `test_integration.py` | real sockets end to end |
| 7 | `test_entrypoints.py` | the command line |
| 3 | `test_package.py` | packaging, interpreter guard |
| 2 | `test_model_has_no_tkinter.py` | the architectural boundary |

Roughly 5,700 lines of source to 3,800 lines of tests.

### 8.2 Measurements

Taken with `python -m demo.bench` on Windows 11, Python 3.11, loopback,
server otherwise idle.

**Latency**

| Path | min | p50 | p95 | p99 | max |
|---|---|---|---|---|---|
| `PING` → `PONG` | 0.50ms | 0.54ms | 0.65ms | 1.10ms | 1.20ms |
| `alice` → `bob` | 0.48ms | 0.54ms | 0.65ms | 1.03ms | 1.06ms |

A message is stored *and* delivered in well under a millisecond. Over a real
network the link adds 10-40ms, so the server is not what a user perceives as
delay until it is heavily loaded.

**Throughput**

25 senders × 200 messages = 5,000 messages, all received, in 0.81s →
**6,168 messages/second** through one server. Every one is written to sqlite
before delivery, so this is the storage rate as much as the routing rate.

**Concurrent connections**

| Clients | Connected | Time | Server threads | Fan-out |
|--------:|----------:|-----:|---------------:|--------:|
| 50 | 50 | 0.55s | 252 | 1.4ms |
| 100 | 100 | 1.25s | 502 | 1.3ms |
| 200 | 200 | 2.79s | 1,002 | 1.7ms |
| 400 | **374** | 7.78s | 1,872 | 1.8ms |

**The ceiling is the thread model, not the protocol.** Fan-out latency barely
moves from 50 to 374 clients -- routing is not what breaks. What breaks is
1,872 OS threads in one process. Reaching millions of connections would mean
replacing thread-per-connection with async I/O and running many processes
behind a load balancer, with shared state moved out of process. That is a
different architecture, and it is named here rather than claimed.

### 8.3 Bugs the measurements found

Worth recording because they were invisible to the test suite:

- A client sending faster than its writer drained tore down its own connection
  at exactly 100 messages.
- `close()` discarded everything still queued, so a client that sent a message
  and quit had silently never sent it.

Both were found by the throughput benchmark reporting 0 of 2,000 delivered,
and both are now fixed and covered.

### 8.4 Three bugs the tests could not see

A pattern worth naming, because all three have the same shape: **the tests
each covered one side of a boundary, and nothing covered the boundary.**

| Bug | Why every test passed |
|---|---|
| `Session` did not forward `receipt`, `invite`, or `create_room`'s members | Controller tests used a fake with every method; integration tests drove `ServerConnection` directly. Nothing used the class the GUI actually holds. |
| History was never decrypted -- scrollback rendered as base64 | Controller tests fed plaintext rows; integration tests ran without a keyring. The one combination that breaks -- encryption on *and* history requested -- was never exercised. |
| `RoomDialog`'s `Entry` was bound to the wrong attribute | The test set the variable directly instead of typing into the field, so it passed while the field did nothing. |

None produced an error message. The first was silent because the app runs
under `pythonw`, which has no stderr for Tk to write a traceback to; the
second because base64 is a valid string; the third because Tk accepts any
string as a `textvariable`.

Three things came out of it: a test comparing `Session`'s sending surface to
`ServerConnection`'s method by method, `report_callback_exception` installed
so no Tk callback fails silently again, and a habit of running the real stack
and reading the output rather than trusting green tests.

---

## 9. Evaluation

### 9.1 What works

Everything in the §1 table except room encryption. The system runs, two
windows talk to each other through a real server, messages survive a restart,
and a client reconnects on its own when the server comes back.

### 9.2 What does not

- **Metadata is unprotected.** Inherent to a routing hub, not a bug.
- **Receipts are direct-message only.** Room receipts need per-member state --
  "read by 3 of 5" -- which is a different model, not a bigger version of
  this one.
- **One process.** See §8.2.
- **Search covers only what a client has loaded**, by construction (6.5).
- **No media, push notifications or multi-device.**

### 9.3 What we would do differently -- **[write this yourselves]**

Suggested honest material, in your own words:

- **Tests that do not cross a seam do not test the seam.** §8.4 lists three
  bugs that every test passed through. This is the single most transferable
  thing the project taught.
- **A security document is the worst place to guess.** The threat model
  claimed room messages were encrypted per member when they were plaintext.
  It was corrected to say so, and only then was the gap actually closed. The
  sequence is kept in that file's header deliberately.
- **Encryption forecloses options, and that is the point.** Sealing rooms
  made server-side search impossible -- not harder, impossible. Recognising
  that as a consequence rather than a regression is what §6.5 is for.
- The GUI was built last on purpose, and that decision paid off: the model
  and controller were fully exercised through a console view before a single
  widget existed.

### 9.4 Individual contributions -- **[write this yourselves]**

| Student | Contribution |
|---|---|
| Aiuna | |
| Faiza | |
| Turjo | |
| Aya | |
| Keisha | |

---

## Figures

| Figure | Subject | Source |
|--------|---------|--------|
| 1 | The client, split so the view can arrive last | §2.2 |
| 2 | End-to-end path of one chat line | §4.1 |
| 3 | Frame format | §3.1 |
| 4 | Client session state machine | §5 |
| 5 | The window | `docs/semaphore-window.png` |
| 6 | Connection scaling | §8.2 |

## Reproducing everything in this report

```bash
pytest                    # 316 tests
python -m demo.bench      # the measurements in §8.2
run.bat fresh             # a server and two chat windows, empty database
python -m demo.peek im.db # what the server actually stored
```
