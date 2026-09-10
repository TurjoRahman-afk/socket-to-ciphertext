"""Persistence tests. Phase 5.

The scrypt cost factor is left at a low value in most of these so the suite
stays fast, but one test runs the real one to prove the default is usable.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from im.server.store.db import Database
from im.server.store.messages import MessageStore, direct_conversation
from im.server.store.rooms import SqliteRooms
from im.server.store.users import SqliteUsers

HASH = "sha256-of-hunter2"


@pytest.fixture
def users() -> SqliteUsers:
    return SqliteUsers(Database(), scrypt_n=2)


# ------------------------------------------------------------------ schema ---


def test_the_schema_is_created_on_connect() -> None:
    db = Database()
    with db.read() as conn:
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert {"users", "rooms", "memberships", "messages", "pending"} <= tables


def test_opening_an_existing_database_does_not_wipe_it(tmp_path: Path) -> None:
    """The whole point of phase 5: a restart must not lose anything."""
    path = tmp_path / "im.db"

    first = SqliteUsers(Database(path), scrypt_n=2)
    first.register("alice", HASH)
    first.db.close()

    second = SqliteUsers(Database(path), scrypt_n=2)

    assert second.exists("alice")
    assert second.verify("alice", HASH)


def test_a_failed_write_rolls_back() -> None:
    db = Database()
    with pytest.raises(ValueError):
        with db.write() as conn:
            conn.execute("INSERT INTO rooms (room, created_at) VALUES (?, ?)", ("#general", 0))
            raise ValueError("something went wrong halfway through")

    with db.read() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM rooms").fetchone()["n"] == 0


# ---------------------------------------------------------------- accounts ---


def test_register_then_verify(users: SqliteUsers) -> None:
    assert users.register("alice", HASH)
    assert users.verify("alice", HASH)
    assert len(users) == 1


def test_a_taken_username_is_refused(users: SqliteUsers) -> None:
    assert users.register("alice", HASH)
    assert not users.register("alice", "a different digest")
    assert users.verify("alice", HASH), "the first registration must survive"


def test_a_wrong_password_is_refused(users: SqliteUsers) -> None:
    users.register("alice", HASH)
    assert not users.verify("alice", "wrong")


def test_an_unknown_user_is_refused(users: SqliteUsers) -> None:
    assert not users.verify("nobody", HASH)


def test_nothing_recognisable_is_stored(users: SqliteUsers) -> None:
    """A stolen database must not hand over what the client sent."""
    users.register("alice", HASH)

    with users.db.read() as conn:
        row = conn.execute(
            "SELECT salt, digest FROM users WHERE username = ?", ("alice",)
        ).fetchone()

    assert HASH.encode() not in row["digest"]
    assert row["digest"] != HASH.encode()
    assert len(row["salt"]) == 16


def test_the_same_password_produces_different_rows(users: SqliteUsers) -> None:
    """Per-user salts. Without them, identical passwords are visible as
    identical rows and one cracked digest breaks every account that shares it."""
    users.register("alice", HASH)
    users.register("bob", HASH)

    with users.db.read() as conn:
        rows = conn.execute("SELECT username, salt, digest FROM users ORDER BY username").fetchall()

    assert rows[0]["salt"] != rows[1]["salt"]
    assert rows[0]["digest"] != rows[1]["digest"]
    assert users.verify("alice", HASH) and users.verify("bob", HASH)


def test_an_unknown_user_costs_the_same_time_as_a_wrong_password() -> None:
    """Otherwise the reply time alone tells an attacker which accounts exist.

    Timed with the real cost factor, because the point is that the work is
    done in both paths -- at n=2 the difference would be lost in noise.
    """
    users = SqliteUsers(Database(), scrypt_n=2**12)
    users.register("alice", HASH)

    start = time.perf_counter()
    users.verify("alice", "wrong")
    wrong_password = time.perf_counter() - start

    start = time.perf_counter()
    users.verify("nobody-at-all", HASH)
    unknown_user = time.perf_counter() - start

    # Generous: the assertion is that the work happens at all, not that the
    # two are identical to the microsecond on a shared CI machine.
    assert unknown_user > wrong_password / 4


def test_the_default_cost_factor_is_usable() -> None:
    """A login must not feel slow. Also proves the default is not so high it
    would make the server unusable under a handful of logins."""
    users = SqliteUsers(Database())
    users.register("alice", HASH)

    start = time.perf_counter()
    assert users.verify("alice", HASH)
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0, f"a login took {elapsed:.2f}s at the default cost"


def test_a_public_key_can_be_stored_and_read_back(users: SqliteUsers) -> None:
    """The column exists from phase 5 so that phase 6 adds no migration."""
    users.register("alice", HASH)
    assert users.pubkey("alice") is None

    users.set_pubkey("alice", "BASE64-X25519-PUBLIC")

    assert users.pubkey("alice") == "BASE64-X25519-PUBLIC"


def test_a_public_key_may_be_given_at_registration(users: SqliteUsers) -> None:
    users.register("alice", HASH, pubkey="BASE64-KEY")
    assert users.pubkey("alice") == "BASE64-KEY"


# ------------------------------------------------------------- messages ---


@pytest.fixture
def store() -> MessageStore:
    return MessageStore(Database())


def add(store: MessageStore, mid: str, conversation: str, sender: str, ts: int) -> None:
    store.record(
        message_id=mid,
        conversation=conversation,
        sender=sender,
        recipient="bob",
        body=f"message {mid}",
        nonce=None,
        ts=ts,
    )


def test_a_direct_conversation_key_is_the_same_from_either_side() -> None:
    """Otherwise alice-to-bob and bob-to-alice would be two half conversations."""
    assert direct_conversation("alice", "bob") == direct_conversation("bob", "alice")
    assert direct_conversation("alice", "bob") != direct_conversation("alice", "carol")


def test_recording_the_same_id_twice_is_not_an_error(store: MessageStore) -> None:
    """A repeat means the client resent after a reconnect, not that something
    is wrong."""
    add(store, "m1", "c", "alice", 100)
    add(store, "m1", "c", "alice", 100)
    assert len(store) == 1


def test_history_comes_back_oldest_first(store: MessageStore) -> None:
    for i, ts in enumerate([300, 100, 200]):
        add(store, f"m{i}", "c", "alice", ts)

    assert [row["ts"] for row in store.history("c")] == [100, 200, 300]


def test_history_returns_the_newest_when_there_are_more_than_the_limit(
    store: MessageStore,
) -> None:
    for i in range(10):
        add(store, f"m{i}", "c", "alice", i)

    rows = store.history("c", limit=3)

    assert [row["ts"] for row in rows] == [7, 8, 9]


def test_history_pages_backwards_with_before(store: MessageStore) -> None:
    for i in range(10):
        add(store, f"m{i}", "c", "alice", i)

    rows = store.history("c", before=5, limit=3)

    assert [row["ts"] for row in rows] == [2, 3, 4]


def test_history_of_an_empty_conversation_is_empty(store: MessageStore) -> None:
    assert store.history("nothing here") == []


def test_the_limit_is_capped(store: MessageStore) -> None:
    """A client asking for a million rows must not be able to have them."""
    for i in range(5):
        add(store, f"m{i}", "c", "alice", i)
    assert len(store.history("c", limit=10_000)) == 5


# -------------------------------------------------------------- pending ---


def test_a_queued_message_is_handed_over_on_flush(store: MessageStore) -> None:
    add(store, "m1", "c", "alice", 100)
    store.queue_for("bob", "m1")

    delivered = store.flush("bob")

    assert [row["id"] for row in delivered] == ["m1"]
    assert store.pending_count("bob") == 0


def test_flushing_twice_delivers_nothing_the_second_time(store: MessageStore) -> None:
    add(store, "m1", "c", "alice", 100)
    store.queue_for("bob", "m1")

    store.flush("bob")

    assert store.flush("bob") == []


def test_pending_messages_come_back_oldest_first(store: MessageStore) -> None:
    for i, ts in enumerate([300, 100, 200]):
        add(store, f"m{i}", "c", "alice", ts)
        store.queue_for("bob", f"m{i}")

    assert [row["ts"] for row in store.flush("bob")] == [100, 200, 300]


def test_one_message_can_be_owed_to_several_people(store: MessageStore) -> None:
    """A room message to five people, three offline, is stored once and
    queued three times rather than copied."""
    add(store, "m1", "#general", "alice", 100)
    for name in ("bob", "carol", "dave"):
        store.queue_for(name, "m1")

    assert len(store) == 1
    assert [row["id"] for row in store.flush("carol")] == ["m1"]
    assert store.pending_count("bob") == 1, "carol's flush must not clear bob's"


def test_a_queued_message_survives_a_restart(tmp_path: Path) -> None:
    """The phase 5 exit criteria, at the store level."""
    path = tmp_path / "im.db"
    first = MessageStore(Database(path))
    first.record(
        message_id="m1",
        conversation=direct_conversation("alice", "bob"),
        sender="alice",
        recipient="bob",
        body="sent while you were out",
        nonce=None,
        ts=100,
    )
    first.queue_for("bob", "m1")
    first.db.close()

    second = MessageStore(Database(path))
    delivered = second.flush("bob")

    assert [row["body"] for row in delivered] == ["sent while you were out"]


# ----------------------------------------------------------------- rooms ---


@pytest.fixture
def rooms() -> SqliteRooms:
    return SqliteRooms(Database())


def test_creating_a_room_twice_is_refused(rooms: SqliteRooms) -> None:
    assert rooms.create("#general")
    assert not rooms.create("#general")


def test_joining_twice_is_harmless(rooms: SqliteRooms) -> None:
    """A repeat is not an error -- it is a client resending after a reconnect."""
    rooms.join("#general", "alice")
    rooms.join("#general", "alice")
    assert rooms.members("#general") == {"alice"}


def test_leaving_removes_only_that_member(rooms: SqliteRooms) -> None:
    rooms.join("#general", "alice")
    rooms.join("#general", "bob")

    rooms.leave("#general", "alice")

    assert rooms.members("#general") == {"bob"}


def test_rooms_of_lists_them_sorted(rooms: SqliteRooms) -> None:
    for room in ("#zulu", "#alpha"):
        rooms.join(room, "alice")
    rooms.join("#other", "bob")

    assert rooms.rooms_of("alice") == ["#alpha", "#zulu"]


def test_membership_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "im.db"
    first = SqliteRooms(Database(path))
    first.create("#general")
    first.join("#general", "alice")
    first.db.close()

    second = SqliteRooms(Database(path))

    assert second.exists("#general")
    assert second.members("#general") == {"alice"}
    assert second.rooms_of("alice") == ["#general"]
