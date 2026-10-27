"""Test-only ASGI app backed by the isolated browser-fixture directory."""

from __future__ import annotations

import os
from pathlib import Path

import backend.storage as storage_module

project_root = Path(os.environ["MUFFIN_E2E_PROJECT_ROOT"]).resolve()
storage_module.ROOT = project_root
storage_module.EPISODES_DIR = project_root / "data" / "episodes"
storage_module.SIMULATIONS_DIR = project_root / "data" / "simulations"
storage_module.IMAGES_DIR = project_root / "src" / "assets" / "images"
storage_module.CHARACTER_MEMORY_DIR = project_root / "data" / "character_memory"
storage_module.PHOTO_CONTROL_DIR = project_root / "data" / "photo_control"

from backend.admin.app import create_admin_app  # noqa: E402

app = create_admin_app(project_root=project_root)
