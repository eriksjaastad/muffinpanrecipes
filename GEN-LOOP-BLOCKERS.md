# Gen Loop Blockers

## 2026-03-18
- `uv run pytest tests/test_scripts.py` failed even with `UV_CACHE_DIR=$TMPDIR/uv-cache`: uv panicked (`Attempted to create a NULL object`, Tokio executor panic). Command output indicates an internal uv crash.
- `uv run pytest tests/test_pick_concept.py` failed even with `UV_CACHE_DIR=$TMPDIR/uv-cache`: same uv panic (`Attempted to create a NULL object`, Tokio executor panic).
- `./venv/bin/python -m pytest tests/test_scripts.py` failed: pytest not installed in project venv.
