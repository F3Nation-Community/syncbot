"""Pytest configuration: default DB backend for unit tests (no live DB required)."""

import os

# Unit tests use MySQL-style env vars without a real server; keep mysql backend.
os.environ.setdefault("DATABASE_BACKEND", "mysql")
