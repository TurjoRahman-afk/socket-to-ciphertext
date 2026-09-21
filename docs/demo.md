# Demonstration runbook

How to show Semaphore working, in the order that makes the point. Roughly ten
minutes, or five if the group before you overran.

The principle throughout: **show the thing, do not describe it.** Every step
below is something the audience watches happen, not something they are asked
to believe.

---

## Before you start

```bash
run.bat fresh
```

`fresh` wipes the database and the key files, so the demo starts from nothing
and the audience sees registration really happen. Two windows open, signed in
as `aya` and `keisha`, with a server window behind them.

**Rehearse this once.** The first run after a wipe has to generate two X25519
keypairs and create the database, which takes a couple of seconds — fine when
you expect it, alarming when you do not.

Have a terminal open in the project directory, ready for the `peek` command in
step 5.

---

## 1. It is a real program, not a mock (30 seconds)

Type a message in one window. It appears in the other.

Worth saying out loud while you do it: these are **two separate processes**
talking over a TCP socket to a **third** process. Nothing here is a simulation
of a network.

Point at the tick under your message — one tick sent, two delivered, two in
orange once they open the conversation.

## 2. Presence and typing (30 seconds)

Start typing in one window without sending. The other shows "aya is typing…".

These are relayed and never stored. A typing indicator that arrived late would
be worse than none.

## 3. Rooms, and who is in them (1 minute)

Click **Rooms**. The dialog asks for a name and shows a tick-list of contacts
with a presence dot each.

Make a room with both people in it and send a message to it.

Two things to point out:

- The room was created **with its members**, not created empty and joined
  afterwards. The protocol carries the member list.
- Room names cannot contain spaces. Type `study group` first, deliberately, to
  show the dialog catching it before anything is sent.

## 4. It survives the server dying (1 minute)

**Close the server window.**

Both clients drop to `RETRYING` and the composer greys out — the interface
cannot offer an action the connection would reject, because both read the same
state machine.

Now restart it:

```bash
.venv\Scripts\python.exe -m im.server --db im.db
```

The clients reconnect on their own, with exponential backoff and jitter. Send
a message to prove the session really came back.

If you have time: send a message to somebody while their window is closed,
then reopen it. The queued message arrives on login.

## 5. The server cannot read any of it (2 minutes — **the one that lands**)

With messages on screen in both windows, run:

```bash
.venv\Scripts\python.exe -m demo.peek im.db
```

The audience has just watched two people read each other's messages. Now they
watch the server's own copy of those same messages turn out to be base64
noise. Nothing about that is arguable.

Point out, in this order:

1. **The accounts table** holds no password and no private key. Only a salt
   and an scrypt digest.
2. **A direct message** is one ciphertext.
3. **A room message** is listed as one sealed copy per member, with each
   member named. A frame carries one body and a room has one key per member,
   so the audience can count the envelopes and see the server holding every
   one of them and able to open none. It looks like this:

   ```
     aya -> #study   ENCRYPTED
       one sealed copy per member, 2 of them:
         for aya        3lVLM9JoGdd06U89Mg806Xz/c8Pdl+0APlOWnLmLR75W+ALou7eg
         for keisha     utknQ3LQXeDymqH+kzdDwuHMTq+OtWsuw8NdsDre9lSZAI9BHelc
   ```

If someone asks why not just encrypt the room once: because then every member
would need the same key, and handing one key to five people is how you end up
handing it to six.

## 6. Search, and why it is client-side (1 minute)

Click **Search** and find a word from earlier.

Then say the interesting part: **the server cannot do this.** Searching what
it stores would mean giving it the keys. Encrypting the rooms did not make
search harder, it made server-side search *impossible* — so the search runs
over what this client has decrypted, and says so plainly when it finds
nothing.

This is a good answer to "what did encryption cost you?", which is a question
worth being asked.

## 7. If asked: over a real network (1 minute)

The demo runs on one machine for reliability, not because it has to.

**Two machines on the same network** — verified working:

```bash
# on the machine that will host, find its address with ipconfig
.venv\Scripts\python.exe -m im.server --host 0.0.0.0 --tls --db im.db

# on the other machine
.venv\Scripts\python.exe -m im.client --view tk --user aya --password demo \
    --host <that address> --tls --cacert dev.crt --register
```

`--tls` generates a self-signed development certificate on first use. Copy
`dev.crt` to the client machine; it is the certificate the client is told to
trust. This has been tested end to end over a real network interface, direct
messages and rooms both.

**Across the internet** needs a tunnel, because a home router will not accept
an inbound connection:

```bash
cloudflared tunnel --url tcp://localhost:5000
```

then point the client at the hostname it prints. The certificate is issued for
`localhost`, and the client's `--server-hostname` defaults to `localhost`, so
the name being checked stays correct however the connection was routed --
which is exactly why that is a separate flag from `--host`.

> **Say this honestly if asked:** the tunnel step is documented but has not
> been run — it needs a tunnelling client that is not installed here. The
> networking it depends on *has* been verified: TLS over a non-loopback
> address, which is the part the code is responsible for. A tunnel is a pipe;
> it does not change the protocol.

---

## Questions worth having an answer ready for

**"Why threads and not asyncio?"**
Chat is I/O-bound, so a thread blocked in `recv()` costs nothing but memory.
The measured ceiling is about 374 connections on one process — and the fan-out
latency barely moves between 50 and 374, so what breaks is 1,872 OS threads,
not the routing. Millions of connections means async I/O and many processes,
which is a different architecture and is named as such in the report.

**"How fast is it?"**
p50 delivery 0.54ms, stored and delivered. About 6,000 messages a second
through one server. Reproduce with `python -m demo.bench`.

**"What does it not protect?"**
Metadata. The server knows who talks to whom and when, because it routes. With
every body sealed that is the largest remaining exposure, and no amount of
encryption fixes it. Also no forward secrecy and no key verification. All of
it is in `docs/threat-model.md`, including the fact that an earlier version of
that file claimed room encryption before it existed.

**"Did anything go wrong?"**
Yes, and the interesting ones were invisible. Three bugs passed the entire
test suite because each test covered one side of a boundary and nothing
covered the boundary itself — `docs/design.md` §8.4. Read that section before
the demo; it is the strongest thing in the report.

---

## Recovery

| If | Do |
|---|---|
| A window will not open | `run.bat fresh` again; the first run generates keys |
| Clients stuck in RETRYING | The server window is closed. Restart it; they reconnect on their own |
| `run.bat` does nothing | The virtual environment is missing — the script says how to make it |
| A message will not decrypt | Both clients need each other's public keys. `run.bat fresh` clears stale ones |
| Everything is broken | `pytest` still passes in under twenty seconds, and that is a reasonable thing to show instead |
