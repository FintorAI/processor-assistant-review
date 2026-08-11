"""Ensure the repo root (where encompass_client.py lives) is importable
regardless of how pytest is invoked (plain `pytest`, `python -m pytest`, or
from a different cwd)."""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
