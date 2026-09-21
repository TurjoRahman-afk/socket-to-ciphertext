"""Cryptography tests. Phase 6.

Nothing here checks that AES-GCM is correct -- that is the `cryptography`
library's job. What is checked is that this project uses it correctly: that
both sides derive the same key, that tampering is detected, and that a nonce
is never reused.
"""

from __future__ import annotations

import ssl
import threading
from pathlib import Path

import pytest

from im.crypto.envelope import DecryptionFailed, key_for, open_, seal
from im.crypto.identity import Identity
from im.crypto.keyring import Keyring
from im.crypto.tls import client_context, generate_self_signed, server_context


@pytest.fixture
def alice() -> Identity:
    return Identity.generate()


@pytest.fixture
def bob() -> Identity:
    return Identity.generate()


# ---------------------------------------------------------------- identity ---


def test_two_identities_differ() -> None:
    assert Identity.generate().public_b64 != Identity.generate().public_b64


def test_both_sides_derive_the_same_secret(alice: Identity, bob: Identity) -> None:
    """The whole point of the exchange: a shared secret neither of them sent."""
    assert alice.exchange(bob.public_b64) == bob.exchange(alice.public_b64)


def test_a_third_party_derives_something_different(alice: Identity, bob: Identity) -> None:
    carol = Identity.generate()
    assert carol.exchange(bob.public_b64) != alice.exchange(bob.public_b64)


def test_a_key_survives_a_save_and_load(tmp_path: Path, bob: Identity) -> None:
    path = tmp_path / "identity.key"
    original = Identity.generate()
    original.save(path)

    reloaded = Identity.load(path)

    assert reloaded.public_b64 == original.public_b64
    assert reloaded.exchange(bob.public_b64) == original.exchange(bob.public_b64)


def test_load_or_create_reuses_an_existing_key(tmp_path: Path) -> None:
    """Generating a new one would silently make every message anybody had
    already sent to this user undecryptable."""
    path = tmp_path / "identity.key"

    first = Identity.load_or_create(path)
    second = Identity.load_or_create(path)

    assert first.public_b64 == second.public_b64


def test_load_or_create_makes_one_when_there_is_none(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "identity.key"
    identity = Identity.load_or_create(path)
    assert path.exists()
    assert len(identity.public_bytes) == 32


# ---------------------------------------------------------------- envelope ---


def test_a_message_round_trips(alice: Identity, bob: Identity) -> None:
    to_bob = key_for(alice, bob.public_b64)
    to_alice = key_for(bob, alice.public_b64)

    ciphertext, nonce = seal(to_bob, "hello 你好 🔐")

    assert open_(to_alice, ciphertext, nonce) == "hello 你好 🔐"


def test_the_ciphertext_does_not_contain_the_plaintext(alice: Identity, bob: Identity) -> None:
    key = key_for(alice, bob.public_b64)
    ciphertext, _ = seal(key, "the secret word is swordfish")
    assert "swordfish" not in ciphertext


def test_every_message_gets_a_fresh_nonce(alice: Identity, bob: Identity) -> None:
    """Reusing a nonce under one key is the worst thing that can be done with
    AES-GCM: it can expose both plaintexts and the authentication key."""
    key = key_for(alice, bob.public_b64)

    nonces = {seal(key, "same message every time")[1] for _ in range(200)}

    assert len(nonces) == 200


def test_the_same_text_encrypts_differently_each_time(alice: Identity, bob: Identity) -> None:
    """Otherwise an observer could tell that two messages were identical."""
    key = key_for(alice, bob.public_b64)
    first, _ = seal(key, "yes")
    second, _ = seal(key, "yes")
    assert first != second


def test_a_stranger_cannot_read_it(alice: Identity, bob: Identity) -> None:
    carol = Identity.generate()
    ciphertext, nonce = seal(key_for(alice, bob.public_b64), "for bob only")

    with pytest.raises(DecryptionFailed):
        open_(key_for(carol, bob.public_b64), ciphertext, nonce)


def test_a_tampered_ciphertext_is_rejected(alice: Identity, bob: Identity) -> None:
    """AES-GCM is authenticated, which is what stops the server quietly
    rewriting a message it cannot read."""
    key = key_for(alice, bob.public_b64)
    ciphertext, nonce = seal(key, "transfer 10 pounds")

    flipped = list(ciphertext)
    flipped[5] = "A" if flipped[5] != "A" else "B"

    with pytest.raises(DecryptionFailed):
        open_(key, "".join(flipped), nonce)


def test_a_tampered_nonce_is_rejected(alice: Identity, bob: Identity) -> None:
    key = key_for(alice, bob.public_b64)
    ciphertext, nonce = seal(key, "hello")

    other = seal(key, "hello")[1]

    with pytest.raises(DecryptionFailed):
        open_(key, ciphertext, other)


def test_associated_data_is_authenticated(alice: Identity, bob: Identity) -> None:
    """Binding the sender's name into the tag means the server cannot relabel
    a message as coming from somebody else."""
    key = key_for(alice, bob.public_b64)
    ciphertext, nonce = seal(key, "hello", associated="alice")

    assert open_(key, ciphertext, nonce, associated="alice") == "hello"
    with pytest.raises(DecryptionFailed):
        open_(key, ciphertext, nonce, associated="carol")


def test_rubbish_input_raises_the_same_error(alice: Identity, bob: Identity) -> None:
    """One exception for every failure. Distinguishing them would let an
    attacker probe the ciphertext one guess at a time."""
    key = key_for(alice, bob.public_b64)
    with pytest.raises(DecryptionFailed):
        open_(key, "not base64 at all!!", "nor this")


# --------------------------------------------------------------------- TLS ---


def test_a_development_certificate_is_generated_once(tmp_path: Path) -> None:
    cert, key = generate_self_signed(tmp_path / "dev.crt", tmp_path / "dev.key")
    assert cert.exists() and key.exists()

    before = cert.read_bytes()
    generate_self_signed(cert, key)

    assert cert.read_bytes() == before, "an existing certificate must be reused"


def test_a_tls_handshake_completes(tmp_path: Path) -> None:
    """A real handshake over a real socket, with the client verifying the
    certificate rather than skipping verification."""
    import socket

    cert, key = generate_self_signed(tmp_path / "dev.crt", tmp_path / "dev.key")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    host, port = listener.getsockname()

    received: list[bytes] = []

    def serve() -> None:
        conn, _ = listener.accept()
        with server_context(cert, key).wrap_socket(conn, server_side=True) as tls:
            received.append(tls.recv(1024))
            tls.sendall(b"pong")

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    context = client_context(cert)
    with socket.create_connection((host, port), timeout=5) as raw:
        with context.wrap_socket(raw, server_hostname="localhost") as tls:
            tls.sendall(b"ping")
            assert tls.recv(1024) == b"pong"

    thread.join(timeout=2)
    listener.close()
    assert received == [b"ping"]


def test_an_untrusted_certificate_is_refused(tmp_path: Path) -> None:
    """The client must not accept a certificate nobody vouched for."""
    import socket

    cert, key = generate_self_signed(tmp_path / "dev.crt", tmp_path / "dev.key")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    host, port = listener.getsockname()

    def serve() -> None:
        conn, _ = listener.accept()
        try:
            server_context(cert, key).wrap_socket(conn, server_side=True)
        except OSError:
            pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    # No load_verify_locations, so this certificate is unknown to the client.
    context = client_context()
    with socket.create_connection((host, port), timeout=5) as raw:
        with pytest.raises(ssl.SSLError):
            context.wrap_socket(raw, server_hostname="localhost")

    thread.join(timeout=2)
    listener.close()


# ---------------------------------------------------------------- keyring ---


def test_a_keyring_publishes_its_own_public_key(alice: Identity) -> None:
    assert Keyring(alice).public_b64 == alice.public_b64


def test_sealing_without_the_key_returns_nothing(alice: Identity) -> None:
    """The caller must ask for the key rather than fall back to plaintext."""
    assert Keyring(alice).seal("bob", "hello", "alice") is None


def test_two_keyrings_can_talk(alice: Identity, bob: Identity) -> None:
    a, b = Keyring(alice), Keyring(bob)
    a.remember("bob", bob.public_b64)
    b.remember("alice", alice.public_b64)

    ciphertext, nonce = a.seal("bob", "hello 你好 🔐", "alice")

    assert b.open("alice", ciphertext, nonce, "alice") == "hello 你好 🔐"


def test_a_relabelled_sender_fails_to_decrypt(alice: Identity, bob: Identity) -> None:
    """The sender's name is authenticated, so the server cannot claim a
    message came from somebody else."""
    a, b = Keyring(alice), Keyring(bob)
    a.remember("bob", bob.public_b64)
    b.remember("alice", alice.public_b64)
    ciphertext, nonce = a.seal("bob", "hello", "alice")

    with pytest.raises(DecryptionFailed):
        b.open("alice", ciphertext, nonce, "carol")


def test_rooms_are_encryptable_too() -> None:
    """Sealed once per member. This asserted the opposite until room
    encryption was implemented."""
    assert Keyring.encryptable("bob")
    assert Keyring.encryptable("#general")


def test_a_changed_key_discards_the_derived_one(alice: Identity, bob: Identity) -> None:
    ring = Keyring(alice)
    ring.remember("bob", bob.public_b64)
    first = ring._key("bob")

    ring.remember("bob", Identity.generate().public_b64)

    assert ring._key("bob") != first


# --------------------------------------------------------------- room sealing ---


def test_a_room_message_is_sealed_once_per_member(alice: Identity, bob: Identity) -> None:
    carol = Identity.generate()
    ring = Keyring(alice, me="alice")
    ring.remember("bob", bob.public_b64)
    ring.remember("carol", carol.public_b64)

    envelopes = ring.seal_for_members(["bob", "carol"], "friday at six", "alice")

    assert envelopes is not None
    assert set(envelopes) == {"alice", "bob", "carol"}, "the sender gets a copy too"


def test_each_member_can_open_only_their_own_envelope(alice: Identity, bob: Identity) -> None:
    carol = Identity.generate()
    sender = Keyring(alice, me="alice")
    sender.remember("bob", bob.public_b64)
    sender.remember("carol", carol.public_b64)
    envelopes = sender.seal_for_members(["bob", "carol"], "friday at six", "alice")

    bobs = Keyring(bob, me="bob")
    bobs.remember("alice", alice.public_b64)
    ciphertext, nonce = envelopes["bob"]
    assert bobs.open("alice", ciphertext, nonce, "alice") == "friday at six"

    # Carol's envelope is not Bob's to read.
    other, other_nonce = envelopes["carol"]
    with pytest.raises(DecryptionFailed):
        bobs.open("alice", other, other_nonce, "alice")


def test_the_sender_can_reopen_their_own_room_message(alice: Identity, bob: Identity) -> None:
    """Without this, your own room history is unreadable after a restart."""
    ring = Keyring(alice, me="alice")
    ring.remember("bob", bob.public_b64)

    envelopes = ring.seal_for_members(["bob"], "friday at six", "alice")
    ciphertext, nonce = envelopes["alice"]

    assert ring.open("alice", ciphertext, nonce, "alice") == "friday at six"


def test_sealing_a_room_is_all_or_nothing(alice: Identity, bob: Identity) -> None:
    """Sending only to the members we hold keys for would drop the rest out
    of the conversation with no sign that it had happened."""
    ring = Keyring(alice, me="alice")
    ring.remember("bob", bob.public_b64)

    assert ring.seal_for_members(["bob", "carol"], "hello", "alice") is None


def test_missing_keys_names_who_is_not_reachable(alice: Identity, bob: Identity) -> None:
    ring = Keyring(alice, me="alice")
    ring.remember("bob", bob.public_b64)

    assert ring.missing_keys(["bob", "carol", "dave"]) == ["carol", "dave"]


def test_two_members_never_share_a_nonce(alice: Identity, bob: Identity) -> None:
    """They do not share a key either, and that pairing is what makes nonce
    reuse catastrophic rather than merely untidy."""
    carol = Identity.generate()
    ring = Keyring(alice, me="alice")
    ring.remember("bob", bob.public_b64)
    ring.remember("carol", carol.public_b64)

    envelopes = ring.seal_for_members(["bob", "carol"], "hello", "alice")
    nonces = [nonce for _ciphertext, nonce in envelopes.values()]

    assert len(set(nonces)) == len(nonces)
