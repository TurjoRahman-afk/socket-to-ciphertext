# Threat model

> **Status: final.** Stating honestly what the system does *not* protect is
> worth more than an overclaim. An earlier revision of this file claimed room
> messages were encrypted per member. They are not, and the tables below have
> been corrected -- the claim is recorded here rather than quietly deleted,
> because a security document that has been wrong once should say so.

## Two kinds of conversation

Nothing below makes sense without this distinction, so it comes first.

| | Direct message | Room message |
|---|---|---|
| End-to-end encrypted | **yes** | **no** |
| What the server holds | base64 ciphertext | the text you typed |
| What the database holds | base64 ciphertext | the text you typed |

`Keyring.encryptable()` returns `False` for any target beginning with `#`, so
the room path in `ChatController.send` takes the plaintext branch. This is a
real gap, not a configuration choice, and everything below is qualified by it.

## Two layers, and neither is automatic

The system has link-layer protection (TLS) and message-layer protection
(end-to-end encryption). They are independent, and **both are optional**:

- **TLS** is enabled by `--tls` on the server and the client. `run.bat` does
  not pass it, so the default demo runs in **plaintext on the wire**.
- **End-to-end encryption** applies to direct messages only, and only once the
  recipient's public key has been fetched.

A claim of "encrypted" with neither qualifier is the kind of thing this
document exists to prevent.

## What the design protects, and what it does not

Read as: the default `run.bat` demo, then what changes with `--tls`.

| Threat | Direct message | Room message |
|--------|----------------|--------------|
| Someone on the same wifi reads your chat | body safe (E2EE); **metadata exposed** without `--tls` | **exposed** without `--tls`; safe from this attacker with it |
| The tunnel or hosting provider reads it | body safe (E2EE) | **exposed** unless `--tls`, and then only from third parties |
| Your own server operator reads it | **no** -- the hub holds ciphertext only | **yes, they read everything** -- TLS does not help, it terminates at the server |
| Someone steals the database file | body safe; passwords scrypt-hashed with a per-user salt | **exposed** -- room rows are plaintext |
| Server learns *who* talks to *whom*, and when | **no** | **no** |
| Observer sees that you connected at all | **no** | **no** |
| Someone with your unlocked laptop reads history | **no** | **no** |

The third row is the one that matters most. End-to-end encryption exists
specifically to defend against the server, and for rooms it does not.

## Mechanism

At registration each client generates an X25519 keypair, keeps the private
half in a local file, and uploads only the public half. To message someone:
fetch their public key, perform an ECDH exchange, run the shared secret
through HKDF, and encrypt the body with AES-GCM under a fresh nonce per
message. The server sees `body` as base64 noise.

The sender's name is authenticated alongside the ciphertext, so the server
cannot relabel a message as coming from somebody else without the recipient's
decryption failing.

TLS, where enabled, is a separate and independent layer. It protects the
framing and metadata from third parties on the network; it does not protect
anything from the server, because that is where it terminates.

## Known limitations

- **Room messages are not encrypted.** The honest fix is to seal the body
  once per member, which is wasteful at scale and entirely reasonable for a
  room of five. It is not implemented.
- **TLS is off unless asked for.** The default demo is plaintext on the wire.
- **No forward secrecy.** Compromising a long-term private key exposes past
  messages. Real systems ratchet; this one does not.
- **No key verification.** A malicious server could substitute its own public
  key in a `GET_KEY` reply and read everything. Mitigating this needs an
  out-of-band fingerprint check, which is not implemented.
- **No at-rest protection of the private key file.**
- **Self-signed certificates.** `--tls` generates a development certificate.
  It encrypts, but it authenticates nothing a real deployment would trust.
- This is coursework demonstrating the design, not a production-grade
  messenger, and should not be described as one.

## Rules followed

- All primitives come from the `cryptography` library. No cipher is
  implemented by hand.
- A nonce is never reused under the same key.
- Unknown user and wrong password produce the same error, so the login path
  cannot be used to enumerate accounts.
