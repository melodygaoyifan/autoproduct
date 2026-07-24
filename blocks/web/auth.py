"""autoproduct block: session auth (web) — pre-built, reviewed once.

Usage (stdlib-compatible):
    from blocks_auth import AuthStore
    auth = AuthStore(db_path)          # sqlite-backed users + sessions
    token = auth.register("name", "password")
    token = auth.login("name", "password")
    user  = auth.user_for(token)       # None if invalid/expired
Copy this file into the product as `blocks_auth.py`; do not rewrite it.
"""
import hashlib
import hmac
import os
import sqlite3
import time

_SESSION_TTL = 7 * 86400


class AuthStore:
    def __init__(self, db_path):
        self.db = sqlite3.connect(db_path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS users("
            "id INTEGER PRIMARY KEY, name TEXT UNIQUE, salt BLOB, hash BLOB)")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS sessions("
            "token TEXT PRIMARY KEY, user_id INTEGER, expires REAL)")
        self.db.commit()

    def _hash(self, password, salt):
        return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)

    def register(self, name, password):
        salt = os.urandom(16)
        self.db.execute("INSERT INTO users(name, salt, hash) VALUES(?,?,?)",
                        (name, salt, self._hash(password, salt)))
        self.db.commit()
        return self.login(name, password)

    def login(self, name, password):
        row = self.db.execute(
            "SELECT id, salt, hash FROM users WHERE name=?", (name,)).fetchone()
        if not row or not hmac.compare_digest(row[2], self._hash(password, row[1])):
            return None
        token = os.urandom(24).hex()
        self.db.execute("INSERT INTO sessions VALUES(?,?,?)",
                        (token, row[0], time.time() + _SESSION_TTL))
        self.db.commit()
        return token

    def user_for(self, token):
        row = self.db.execute(
            "SELECT user_id, expires FROM sessions WHERE token=?",
            (token or "",)).fetchone()
        if not row or row[1] < time.time():
            return None
        return row[0]
