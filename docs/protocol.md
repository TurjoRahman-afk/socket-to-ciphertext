# Wire protocol

> **Status: FROZEN as of phase 1.** The server track and the client track now
> proceed in parallel against this document. Changing anything below needs
> agreement from both, and a bump of `v`. Implemented by `im/common/frames.py`
> and `im/common/codec.py`, and covered by `tests/test_codec.py`.

## Framing

TCP is a byte stream with no message boundaries. One `recv()` is not one
message: it may return half a frame, or three frames at once.

- One frame per line, terminated by `\n`.
- Each line is one JSON object, encoded UTF-8.
- The receiver buffers incoming bytes, splits on `\n`, and keeps the remainder
  in the buffer for next time.
- Every file wrapper around a socket is opened with an explicit
  `encoding="utf-8"`. The Windows default codepage mangles non-ASCII text.

## Frame shape

```json
{"v":1,"id":"7f3a...","type":"MSG","ts":1756684800000,
 "from":"alice","to":"#general",
 "body":"BASE64-CIPHERTEXT","n":"BASE64-NONCE"}
```

| Field  | Meaning |
|--------|---------|
| `v`    | Protocol version. Currently `1`. |
| `id`   | Unique frame identifier, used for ACK correlation and deduplication. |
| `type` | One of the message types below. |
| `ts`   | Milliseconds since the Unix epoch, set by the sender. |
| `from` | Sending username. Filled in by the server on relay. |
| `to`   | Recipient username, or a room name prefixed with `#`. |
| `body` | Message payload. Plaintext until phase 6, ciphertext afterwards. |
| `n`    | Base64 AES-GCM nonce. Phase 6 onward. |

After phase 6 only `body` is encrypted. Every routing field stays readable,
because the server has to route what it cannot read.

## Client to server

| Type | Payload | Reply |
|------|---------|-------|
| `REGISTER` | user, pass_hash, pubkey | `OK` / `ERROR` |
| `LOGIN` | user, pass_hash | `LOGIN_OK` + roster + rooms |
| `GET_KEY` | user | `KEY` (their public key) |
| `MSG` | to, body, nonce | `ACK`, then fan-out |
| `CREATE_ROOM` | room, members (optional) | `ROOM_STATE` |
| `JOIN` / `LEAVE` | room | `ROOM_STATE` |
| `INVITE` | room, members | `ROOM_STATE` |
| `TYPING` | to, on / off | relayed only |
| `RECEIPT` | to, ref, state | relayed to the sender |
| `HISTORY` | room, before, limit | `HISTORY_RESULT` |
| `PING` | -- | `PONG` |

## Server to client

| Type | Payload |
|------|---------|
| `MSG` | from, to, body, nonce, ts, id |
| `RECEIPT` | from, ref, state |
| `ROOM_STATE` | room, members |
| `PRESENCE` | user, ONLINE / OFFLINE |
| `ERROR` | code, message |

## Heartbeat

The client sends `PING` on an interval. Two missed `PONG` replies move the
connection out of `ONLINE` and into `RETRYING`, which reconnects with
exponential backoff.

## Limits

| Limit | Value | Why |
|-------|-------|-----|
| Maximum line length | 1 MiB | A peer that opens a connection and streams bytes without ever sending a newline would otherwise grow the receive buffer until the server runs out of memory. Exceeding it closes the connection. |
| Encoding | UTF-8, never escaped | Non-ASCII travels as itself, so a packet trace stays readable during the demo and fewer bytes go on the wire. |
| Line ending | `
` | A trailing `
` is stripped on receipt, so a telnet session on Windows works by hand. |

## Error codes

| Code | Meaning |
|------|---------|
| `BAD_FRAME` | The line was not valid JSON, or not a valid frame. The connection stays open. |
| `LINE_TOO_LONG` | The line-length limit was exceeded. The connection is closed. |

## Notes for implementers

- `from` is a reserved word in Python, so `Frame` names that attribute
  `sender`. This affects only the Python code -- the wire name is `from`.
- Fields that are `None` are omitted from the encoded object rather than sent
  as `null`. A receiver must treat "absent" and "null" identically.
- Type-specific payload (`user`, `room`, `pubkey`, ...) is flattened to the top
  level of the object rather than nested. Those keys may not collide with the
  reserved field names in the table above.
- Never assume one `recv()` returns one frame. Buffer the bytes and split on
  the delimiter; `LineBuffer` is the only place in the project that does this.

## Receipts

A message passes through three states from its sender's point of view.

| State | Means | Set by |
|-------|-------|--------|
| `SENT` | The server accepted and stored it. This is what `ACK` reports. | the server |
| `DELIVERED` | The recipient's client received it and put it in its model. | the recipient's client |
| `READ` | The recipient opened the conversation containing it. | the recipient's client |

A `RECEIPT` frame carries `ref`, the id of the message it concerns, and
`state`, one of `DELIVERED` or `READ`. Rules:

- **Only the recipient may report on a message.** The server looks the
  message up and relays the receipt to its sender alone. Without that,
  anybody who learned a message id could claim somebody else had read it.
- **A receipt never moves the state backwards.** The two can cross on the
  wire, and a `DELIVERED` arriving after a `READ` is discarded.
- **Being read implies having arrived**, even if the delivery receipt was
  lost.
- If the sender is offline the receipt is not queued. The state is stored
  against the message, so `HISTORY` carries it when they return.
- Room messages produce no receipts. One message with twenty members would
  mean forty receipts, and the protocol has nowhere to record twenty separate
  per-member states.

## Rooms and membership

`CREATE_ROOM` may name the members it should start with, which is what the
milestone 1 design specified. Without it, a room can only be filled by telling
each person its name out of band and having them `JOIN` -- which is a worse
protocol and a much worse interface.

A name that has no account is skipped rather than refused. Losing a whole room
to one misspelt name would be worse than a room with one person missing, and
the `ROOM_STATE` that comes back says plainly who made it in. At most 64 names
are read from one frame.

`INVITE` does the same thing to a room that already exists, and only a member
may send it. Otherwise guessing a room name would be enough to add yourself to
it, or to quietly add somebody else. Everyone added is sent `ROOM_STATE`, so a
new member learns of the room without having to ask for it.

## Additional error codes

| Code | Meaning |
|------|---------|
| `NO_KEY` | No public key is published for that user, or no such user. |
| `NOT_A_MEMBER` | Sending to, reading the history of, leaving, or inviting into a room you are not in. |
| `ROOM_EXISTS` | `CREATE_ROOM` for a room that already exists. |
| `BAD_ROOM` | A room name missing its `#`, empty, over 32 characters, or containing whitespace. |
| `BAD_RECEIPT` | A `RECEIPT` without a `ref`, or with a state other than `DELIVERED` or `READ`. |
| `NO_SUCH_ROOM` | `INVITE` naming a room that does not exist. |
| `NO_SUCH_USER` | `INVITE` where not one of the names has an account. |
