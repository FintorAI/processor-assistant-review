"""Ensure the repo root (where encompass_client.py lives) and output/ (where
proc_agent.py / write_graphs.py live) are importable regardless of how
pytest is invoked (plain `pytest`, `python -m pytest`, or from a different
cwd)."""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OUTPUT_DIR = os.path.join(_REPO_ROOT, "output")
for _path in (_REPO_ROOT, _OUTPUT_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)
