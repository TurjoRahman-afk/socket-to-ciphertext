"""Look inside the server's database, live, in front of an audience.

    python -m demo.peek im.db

The point of showing this rather than describing it: the audience watches two
people read each other's messages, and then watches the server's own copy of
those messages turn out to be unreadable. Nothing about that is arguable.
"""

from __future__ import annotations

import sqlite3
import sys

WIDTH = 78


def rule(title: str) -> None:
    print(f"\n{'-' * WIDTH}\n  {title}\n{'-' * WIDTH}")


def peek(path: str) -> int:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.OperationalError as exc:
        print(f"  cannot open {path}: {exc}")
        return 1
    conn.row_factory = sqlite3.Row

    rule("ACCOUNTS  —  no password and no private key is here")
    for row in conn.execute("SELECT username, salt, digest, pubkey FROM users"):
        print(f"\n  {row['username']}")
        print(f"    salt   {row['salt'].hex()}")
        print(f"    digest {row['digest'].hex()}")
        key = row["pubkey"]
        print(f"    public key {key[:44] + '…' if key else '(none published)'}")
    print("\n  -> the salt differs per user, so two people who chose the same")
    print("     password have nothing in common on disk.")

    rule("ROOMS")
    rooms = conn.execute("SELECT room FROM rooms ORDER BY room").fetchall()
    if not rooms:
        print("\n  (none yet)")
    for row in rooms:
        members = conn.execute(
            "SELECT username FROM memberships WHERE room = ? ORDER BY username",
            (row["room"],),
        ).fetchall()
        print(f"\n  {row['room']}: {', '.join(m['username'] for m in members) or '(empty)'}")

    rule("MESSAGES  —  what the server actually holds")
    messages = conn.execute(
        "SELECT sender, recipient, body, nonce FROM messages ORDER BY ts, rowid"
    ).fetchall()
    if not messages:
        print("\n  (nothing sent yet)")
    for row in messages:
        encrypted = bool(row["nonce"])
        print(
            f"\n  {row['sender']} -> {row['recipient']}   "
            f"{'ENCRYPTED' if encrypted else 'plaintext'}"
        )
        print(f"    {row['body']}")
    if any(row["nonce"] for row in messages):
        print("\n  -> the encrypted rows are what the server stores and routes.")
        print("     It has no private key for either party and cannot read them.")
    if any(not row["nonce"] for row in messages):
        print("\n  -> the plaintext rows are room messages, which are not yet")
        print("     end-to-end encrypted. Stated rather than hidden.")

    rule("UNDELIVERED  —  waiting for somebody to come back")
    pending = conn.execute(
        "SELECT p.username, COUNT(*) AS n FROM pending p GROUP BY p.username"
    ).fetchall()
    if not pending:
        print("\n  (nobody is owed anything)")
    for row in pending:
        print(f"\n  {row['username']}: {row['n']} message(s) queued")

    print()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(peek(sys.argv[1] if len(sys.argv) > 1 else "im.db"))
