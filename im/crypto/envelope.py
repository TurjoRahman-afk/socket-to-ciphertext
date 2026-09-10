"""Sealing and opening a message body with AES-GCM.

The shared secret from an X25519 exchange is never used as a key directly.
It is passed through HKDF first, which turns a value with the right length
into one with the right *shape* -- uniformly random, and bound to a context
string so the same two people deriving a key for a different purpose would
get a different key.

AES-GCM is authenticated: opening fails loudly if a single bit of the
ciphertext, the nonce, or the associated data has been altered. That is what
stops the server from quietly rewriting messages it cannot read.
"""

from __future__ import annotations

import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from im.crypto.identity import Identity, b64, unb64

#: Bound into every derived key, so a secret derived here could never be
#: mistaken for one derived for some other purpose later.
INFO = b"semaphore/v1/message"

#: 96 bits, the size AES-GCM is designed around.
NONCE_BYTES = 12
KEY_BYTES = 32


class DecryptionFailed(Exception):
    """The ciphertext, nonce or sender did not match. Never say which."""


def derive_key(shared_secret: bytes, *, info: bytes = INFO) -> bytes:
    """Turn a raw X25519 secret into an AES key."""
    return HKDF(
        algorithm=hashes.SHA256(),
        length=KEY_BYTES,
        salt=None,
        info=info,
    ).derive(shared_secret)


def key_for(identity: Identity, peer_public_b64: str) -> bytes:
    """The message key shared with one other person.

    Both sides compute the same value: X25519 is symmetric in the two halves,
    so alice(private) + bob(public) equals bob(private) + alice(public).
    """
    return derive_key(identity.exchange(peer_public_b64))


def seal(key: bytes, plaintext: str, *, associated: str = "") -> tuple[str, str]:
    """Encrypt a message body. Returns (ciphertext, nonce), both base64.

    A fresh random nonce per message. Reusing one under the same key is the
    single worst thing that can be done with AES-GCM -- it does not merely
    weaken the encryption, it can expose the plaintext of both messages and
    the authentication key with them.
    """
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), associated.encode("utf-8"))
    return b64(ciphertext), b64(nonce)


def open_(key: bytes, ciphertext_b64: str, nonce_b64: str, *, associated: str = "") -> str:
    """Decrypt a message body, or raise DecryptionFailed.

    One exception for every failure, with no detail about which check failed.
    Distinguishing a bad tag from a bad nonce would hand an attacker a way to
    probe the ciphertext one guess at a time.
    """
    try:
        plaintext = AESGCM(key).decrypt(
            unb64(nonce_b64), unb64(ciphertext_b64), associated.encode("utf-8")
        )
    except (InvalidTag, ValueError, TypeError) as exc:
        raise DecryptionFailed("could not decrypt this message") from exc
    return plaintext.decode("utf-8")
