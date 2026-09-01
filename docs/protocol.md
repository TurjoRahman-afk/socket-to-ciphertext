# Wire protocol

> **Status: DRAFT.** This document is frozen at the end of phase 1. Once frozen,
> the server track and the client track can proceed in parallel against it.
> Every teammate signs off before any client code is written.

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
| `CREATE_ROOM` / `JOIN` / `LEAVE` | room | `ROOM_STATE` |
| `TYPING` | to, on / off | relayed only |
| `HISTORY` | room, before, limit | `HISTORY_RESULT` |
| `PING` | -- | `PONG` |

## Server to client

| Type | Payload |
|------|---------|
| `MSG` | from, to, body, nonce, ts, id |
| `PRESENCE` | user, ONLINE / OFFLINE |
| `ERROR` | code, message |

## Heartbeat

The client sends `PING` on an interval. Two missed `PONG` replies move the
connection out of `ONLINE` and into `RETRYING`, which reconnects with
exponential backoff.
