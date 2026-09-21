# Threat model

> **Status: final.** Stating honestly what the system does *not* protect is
> worth more than an overclaim.
>
> This file has been wrong once, and the history is kept rather than tidied
> away. An early revision claimed room messages were encrypted once per member
> when they were in fact plaintext. That was first corrected to say so plainly,
> and then the gap was closed, so rooms really are sealed per member now. The
> order matters: the claim was made true, it was not re-asserted.

## Two kinds of conversation

Both are end-to-end encrypted, by different arrangements of the same
primitive.

| | Direct message | Room message |
|---|---|---|
| End-to-end encrypted | **yes** | **yes** |
| Sealed | once, for the recipient | once per member |
| What the server holds | base64 ciphertext | a map of base64 ciphertexts |
| Keys the server holds | none | none |

A frame carries one body and a room has one key per member, so a room
message's per-member ciphertexts travel beside the frame in `data["env"]`.
The server hands each member the envelope addressed to them and stores the
whole map in one row, so history can make the same split later. It cannot
open any of them.

N ciphertexts for N members is wasteful at scale and entirely reasonable for
a room of five. That cost is the reason the honest alternative -- leaving
rooms in plaintext and documenting the fact -- was rejected rather than
settled for.

**Someone added to a room cannot read what was said before they arrived.** No
envelope was sealed for them, so those rows are skipped. That is what
end-to-end encryption means, not a delivery failure.

## Two layers, and neither is automatic

The system has link-layer protection (TLS) and message-layer protection
(end-to-end encryption). They are independent:

- **TLS** is enabled by `--tls` on the server and the client. `run.bat` does
  not pass it, so the default demo runs in **plaintext on the wire** -- the
  bodies are still sealed, but the framing and metadata are not.
- **End-to-end encryption** applies once the keys it needs have been fetched.
  A direct message needs the recipient's key; a room message needs every
  member's. Until then the message is held, never sent in the clear.

A claim of "encrypted" with neither qualifier is the kind of thing this
document exists to prevent.

## What the design protects, and what it does not

| Threat | Protected? | By what, and what is still exposed |
|--------|-----------|------------------------------------|
| Someone on the same wifi reads your messages | yes | E2EE seals every body. **Without `--tls` they still see who you talk to and when.** |
| The tunnel or hosting provider reads them | yes | E2EE. Same metadata caveat. |
| Your own server operator reads them | yes | E2EE -- the hub holds ciphertext and no keys, for rooms as well as direct messages. TLS would not have helped here; it terminates at the server. |
| Someone steals the database file | yes | Every body is ciphertext; passwords are scrypt-hashed with a per-user salt. |
| Server learns *who* talks to *whom*, and when | **no** | Metadata -- reduced by not logging IPs, not eliminated. |
| Observer sees that you connected at all | **no** | Would need Tor or a mixnet. Out of scope. |
| Someone with your unlocked laptop reads history | **no** | The private key file is not encrypted at rest. |

The third row is the one that matters most, and it is the row this project had
to do real work to earn. End-to-end encryption exists specifically to defend
against the server, and until rooms were sealed per member it only did half
the job.

## Mechanism

At registration each client generates an X25519 keypair, keeps the private
half in a local file, and uploads only the public half. To message someone:
fetch their public key, perform an ECDH exchange, run the shared secret
through HKDF, and encrypt the body with AES-GCM under a fresh nonce per
message. The server sees `body` as base64 noise.

The sender's name is authenticated alongside the ciphertext, so the server
cannot relabel a message as coming from somebody else without the recipient's
decryption failing.

A room message is the same operation repeated: the body is sealed once for
each member under the key shared with that member, with a **fresh nonce per
member as well as per message** -- two members do not share a key, and it is
that pairing which makes nonce reuse catastrophic rather than merely untidy.
A copy is sealed to the sender as well, so their own history stays readable
after a restart.

Sealing is all or nothing. If one member's key is missing the message is held
and the key fetched, because sending to the members whose keys happen to be
held would drop the rest out of the conversation with no sign that it had
happened.

TLS, where enabled, is a separate and independent layer. It protects the
framing and metadata from third parties on the network; it does not protect
anything from the server, because that is where it terminates.

## Known limitations

- **Metadata is not protected at all.** The server necessarily knows who is
  talking to whom and when, because it routes. This is the largest remaining
  exposure, and no amount of body encryption addresses it.
- **TLS is off unless asked for.** The default demo leaves the framing and
  metadata in the clear, though never the bodies.
- **Room encryption does not scale.** One ciphertext per member per message
  is fine for five and wrong for five hundred, which would want a shared
  group key rotated on membership change.
- **Search is limited to what a client has decrypted**, which is the direct
  consequence of the server being unable to read anything. The encryption did
  not break search; it made server-side search impossible by construction.
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
- A nonce is never reused under the same key, and never shared between two
  members of a room.
- Unknown user and wrong password produce the same error, so the login path
  cannot be used to enumerate accounts.
- A key that changes invalidates the derived one, so a substituted key cannot
  quietly reuse a cached secret.
