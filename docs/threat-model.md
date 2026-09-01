# Threat model

> **Status: draft, completed in phase 6.** Stating honestly what the system
> does *not* protect is worth more than an overclaim.

## What the design protects, and what it does not

| Threat | Protected? | By what |
|--------|-----------|---------|
| Someone on the same wifi reads your chat | yes | TLS on the socket |
| The tunnel or hosting provider reads it | yes | TLS, plus E2EE underneath |
| Your own server operator reads it | yes | End-to-end encryption -- the hub holds ciphertext only |
| Someone steals the database file | yes | Stored messages are ciphertext; passwords salted and hashed |
| Server learns *who* talks to *whom*, and when | **no** | Metadata -- reduced by not logging IPs, not eliminated |
| Observer sees that you connected at all | **no** | Would need Tor or a mixnet. Out of scope. |
| Someone with your unlocked laptop reads history | **no** | No at-rest encryption of the local key file |

## Mechanism

At registration each client generates an X25519 keypair, keeps the private half
in a local file, and uploads only the public half. To message someone: fetch
their public key, perform an ECDH exchange, run the shared secret through HKDF,
and encrypt the body with AES-GCM under a fresh nonce per message. The server
sees `body` as base64 noise.

For rooms, the body is encrypted once per member. That is wasteful at scale and
perfectly fine for a room of five -- it is named here as a known limitation
rather than hidden.

## Known limitations

- **No forward secrecy.** Compromising a long-term private key exposes past
  messages. Real systems ratchet; this one does not.
- **No key verification.** A malicious server could substitute its own public
  key in a `GET_KEY` reply and read everything. Mitigating this needs an
  out-of-band fingerprint check, which is not implemented.
- **No at-rest protection of the private key file.**
- This is coursework demonstrating the design, not a production-grade
  messenger, and should not be described as one.

## Rules followed

- All primitives come from the `cryptography` library. No cipher is
  implemented by hand.
- A nonce is never reused under the same key.
