# Semaphore -- Testing Report

**Course:** Computer Software and Big Data Development
**Deliverable:** third submission, 30 September 2026

| Student ID | Name |
|------------|------|
| 1820242075 | Aiuna |
| 1820242085 | Faiza |
| 1820242086 | Turjo |
| 1820242087 | Aya |
| 1820242109 | Keisha |

> Every number in this report was measured on the machine described in §6 and
> can be reproduced with the commands in §7. Where a measurement disagreed
> with what we expected, the measurement is what is written down.

---

## 1. Summary

| | |
|---|---|
| Tests | **352**, all passing |
| Statement coverage | **83%** of `im/` (3,142 statements, 533 not executed) |
| Source | 6,860 lines |
| Test code | 4,481 lines |
| Run time | about 20 seconds for the whole suite |
| Delivery latency | p50 **0.55ms**, p99 1.12ms |
| Throughput | **4,061 messages/second** through one server |
| Concurrent clients | 200 cleanly; **388 of 400** attempted |

---

## 2. Strategy

Two kinds of test, kept deliberately apart.

**Unit tests without sockets.** The router, model, controller, codec and
crypto are tested against fakes. No port is bound and no thread is started,
so they are fast, deterministic, and cannot fail because the machine was
busy. This is only possible because `MessageRouter` imports no `socket`, no
`ssl` and no `threading` -- it reaches a connection through a `Session`
protocol whose `send()` drops a frame on a queue, so a test can pass a fake
that appends to a list. That one design decision is why 84 routing tests run
instantly.

**Integration tests with sockets.** A real server on an ephemeral port, real
clients, real TLS, real reconnection. Sixteen of them, because they are slow
and because most questions do not need them.

Three rules we followed:

1. **A test asserts behaviour, not implementation.** Tests that assert how
   something is done have to be rewritten whenever it is done differently,
   and they stop catching anything.
2. **A bug gets a test before it gets a fix**, so it cannot come back
   unnoticed.
3. **The test name says what should be true**, not what the function is
   called. `test_a_stranger_cannot_invite_anyone` survives a rename;
   `test_invite_2` does not.

---

## 3. What is tested, and why there

| Count | File | What it covers |
|------:|------|----------------|
| 84 | `test_router.py` | every routing rule, membership, contacts, receipts -- no network |
| 46 | `test_model.py` | conversations, unread counts, presence, search |
| 42 | `test_controller.py` | frames to model and back, encryption, opening conversations |
| 30 | `test_store.py` | sqlite, scrypt, history ordering, offline queue |
| 30 | `test_crypto.py` | key agreement, sealing per member, tampering |
| 29 | `test_tk_view.py` | the window, dialogs, the queue bridge, the backdrop |
| 25 | `test_codec.py` | framing, split reads, multi-byte character splits |
| 19 | `test_connection_state.py` | the connection state machine |
| 19 | `test_connection.py` | client connection behaviour |
| 16 | `test_integration.py` | real sockets, end to end |
| 7 | `test_entrypoints.py` | the command line |
| 3 | `test_package.py` | packaging and the interpreter version guard |
| 2 | `test_model_has_no_tkinter.py` | the architectural boundary |
| **352** | | |

Two of those deserve explanation.

**`test_codec.py` spends 25 tests on one small class.** TCP is a byte stream
with no message boundaries: one `recv()` may return half a frame, three
frames, or a frame split in the middle of a multi-byte UTF-8 character.
`LineBuffer` is the only place that has to care, so it is the only place
tested this hard. This is the most common beginner error in socket
programming and we wanted it impossible to reintroduce.

**`test_model_has_no_tkinter.py` is two tests that parse source.** They fail
the build if `im/client/model/` ever imports a GUI library. A comment saying
"keep these separate" decays; a test does not. This is how we can claim the
console view and the window are genuinely interchangeable over one model.

---

## 4. Coverage

83% of statements in `im/`. The distribution matters more than the number:

| Area | Coverage | Comment |
|---|---:|---|
| `common/codec.py` | 100% | the framing, tested hardest for the reason above |
| `crypto/envelope.py` | 100% | every encryption path |
| `model/events.py` | 100% | |
| `store/db.py` | 100% | |
| `client/model/chat.py` | 91% | |
| `server/router.py` | 89% | 366 statements, the largest module |
| `client/net/connection.py` | 80% | the untested parts are socket error paths |
| `client/view/tk/app.py` | 72% | drawing code -- see below |
| `client/view/console.py` | 27% | **deliberately low** |

**Why the console view is at 27% and we are not fixing it.** It is an
interactive read-print loop; testing it properly would mean driving a
terminal, and what it does -- format a model into text -- is already covered
by the model tests underneath it. Raising this number would mean writing
tests that assert the shape of printed strings, which would break every time
the wording changed and would catch nothing. We would rather report 27% with
a reason than 90% with tests that exist to move a number.

**Why `app.py` is at 72%.** Most of the gap is drawing code -- the exact
coordinates a bubble is painted at. We test that the right things are drawn
(`as_text()` on the transcript) rather than where, because where is a design
decision and design decisions change.

---

## 5. The three bugs the tests did not catch

This is the part of the report we think is worth reading.

During this phase, three real bugs reached the running program with the
entire suite passing. All three have the same shape: **the tests covered each
side of a boundary and nothing covered the boundary itself.**

| Bug | Why every test passed |
|---|---|
| `Session` did not forward `receipt`, `invite`, or `create_room`'s members | Controller tests used a fake with every method; integration tests drove `ServerConnection` directly. Nothing exercised the class the GUI actually holds. |
| History was never decrypted -- scrollback rendered as base64 | Controller tests fed plaintext rows; integration tests ran without a keyring. The one combination that breaks -- encryption on **and** history requested -- was never tried. |
| A dialog's text field was bound to the wrong attribute | The test set the variable directly instead of typing into the field, so it passed while the field did nothing. |

**None of the three produced an error message.** The first was silent because
the application launches with `pythonw`, which has no stderr for Tk to write
a traceback to. The second because base64 is a perfectly valid string. The
third because Tk accepts any string as a `textvariable`.

What we changed as a result:

1. **A test that compares two classes**, method by method and signature by
   signature, so `Session` cannot drift from `ServerConnection` again. We
   verified it works by deleting a method and watching it fail.
2. **`report_callback_exception` installed on the window**, so a callback
   that raises shows a dialog instead of vanishing.
3. **A habit**: run the real stack and read the output, rather than trusting
   a green suite. All three were found that way, not by writing more tests.

The lesson we would carry forward: *a test that does not cross a seam does
not test the seam*, and a suite can be both large and blind in exactly the
places that matter.

---

## 6. Performance

Measured with `python -m demo.bench` on **Windows, Python 3.11.9**, over
loopback, server otherwise idle.

> An earlier run of these figures was taken while the coverage suite was
> running on the same machine. Throughput came out at half the real value and
> fan-out at 400 clients read 724ms instead of 1.9ms. Those numbers were
> discarded and the benchmark re-run with nothing else running. We mention it
> because a benchmark that shares a CPU is not measuring what it claims to.

### 6.1 Latency

| Path | min | p50 | p95 | p99 | max |
|---|---|---|---|---|---|
| `PING` → `PONG` | 0.49ms | **0.55ms** | 0.69ms | 1.06ms | 1.39ms |
| `alice` → `bob` | 0.51ms | **0.55ms** | 0.69ms | 1.12ms | 1.31ms |

A message is written to sqlite *and* delivered in well under a millisecond.
The two rows being identical is the useful part: routing and storing a real
message costs no more than answering a ping, so neither is a bottleneck at
this scale. Over a real network the link adds 10--40ms, so the server is not
what a user perceives as delay.

### 6.2 Throughput

25 senders × 200 messages = 5,000 messages, **all 5,000 received**, in 1.23s
→ **4,061 messages/second** through one server. Every one is written to
sqlite before delivery, so this is the storage rate as much as the routing
rate.

### 6.3 Concurrent connections

| Clients | Connected | Time | Server threads | Fan-out |
|--------:|----------:|-----:|---------------:|--------:|
| 50 | 50 | 0.55s | 252 | 1.5ms |
| 100 | 100 | 1.21s | 502 | 1.2ms |
| 200 | 200 | 3.24s | 1,002 | 1.7ms |
| 400 | **388** | 10.00s | 1,954 | 1.9ms |

**The ceiling is the thread model, not the protocol.** Fan-out latency barely
moves from 50 clients to 388 -- 1.5ms to 1.9ms -- so routing is not what
breaks. What breaks is 1,954 OS threads in one process, at two threads per
connection. Reaching far larger numbers would mean replacing
thread-per-connection with async I/O and running many processes behind a load
balancer, with shared state moved out of process. That is a different
architecture, and we name it rather than claim it.

### 6.4 Bugs performance testing found

Two real bugs, neither visible to the test suite:

- A client sending faster than its writer thread drained tore down its own
  connection at exactly 100 queued messages.
- `close()` discarded everything still queued, so a client that sent a
  message and quit had silently never sent it.

Both were found by the throughput benchmark reporting 0 of 2,000 delivered.
Both are fixed and covered by tests now.

---

## 7. Reproducing all of this

```bash
pytest                                   # 352 tests, about 20 seconds
pytest --cov=im --cov-report=term-missing  # the coverage table in section 4
python -m demo.bench                     # the measurements in section 6
pytest tests/test_router.py              # routing only, no sockets, instant
ruff check .                             # lint
```

Run the benchmark with nothing else on the machine, for the reason in §6.

---

## 8. What we would test differently next time

- **Test the seams first.** Everything in §5 came from the joins between
  components, and we tested components.
- **One end-to-end test per feature, through the real stack**, not just the
  layer that was convenient. It would have caught all three bugs in §5.
- **Run the program, not only the suite.** Every bug that reached the running
  application was found by using it.
- **Write down what a low coverage number means** at the time, not
  afterwards. The 27% on the console view is defensible; it took us a while
  to be able to say why.
