"""Persistence tests. Phase 5.

The scrypt cost factor is left at a low value in most of these so the suite
stays fast, but one test runs the real one to prove the default is usable.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from im.server.store.db import Database
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
            conn.execute(
                "INSERT INTO rooms (room, created_at) VALUES (?, ?)", ("#general", 0)
            )
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
