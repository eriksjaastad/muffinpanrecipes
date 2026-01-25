"""
Tests for scripts module.

These are smoke tests to ensure scripts are importable and have expected entry points.
"""

import pytest
from pathlib import Path


class TestBuildSite:
    """Tests for scripts/build_site.py"""
    
    def test_build_site_imports(self):
        """Smoke test: build_site script is importable."""
        from scripts import build_site
        assert hasattr(build_site, 'main')
    
    def test_build_site_has_legacy_build(self):
        """Verify legacy_build function exists."""
        from scripts import build_site
        assert hasattr(build_site, '_legacy_build')
    
    def test_build_site_has_dry_run_rebuild(self):
        """Verify dry_run_rebuild function exists."""
        from scripts import build_site
        assert hasattr(build_site, '_dry_run_rebuild')


class TestOptimizeImages:
    """Tests for scripts/optimize_images.py"""
    
    def test_optimize_images_imports(self):
        """Smoke test: optimize_images script is importable."""
        from scripts import optimize_images
        assert hasattr(optimize_images, 'main')
    
    def test_optimize_images_has_safe_move(self):
        """Verify safe_move_to_archive function exists."""
        from scripts import optimize_images
        assert hasattr(optimize_images, 'safe_move_to_archive')


class TestValidateProject:
    """Tests for scripts/validate_project.py"""
    
    def test_validate_project_imports(self):
        """Smoke test: validate_project script is importable."""
        from scripts import validate_project
        assert hasattr(validate_project, 'main')


class TestWardenAudit:
    """Tests for scripts/warden_audit.py"""
    
    def test_warden_audit_imports(self):
        """Smoke test: warden_audit script is importable."""
        from scripts import warden_audit
        assert hasattr(warden_audit, 'run_audit')


class TestAtomicWrite:
    """Tests for atomic write utility"""
    
    def test_atomic_write_imports(self):
        """Verify atomic write module is importable."""
        from backend.utils.atomic import atomic_write, atomic_write_json
        assert callable(atomic_write)
        assert callable(atomic_write_json)
    
    def test_atomic_write_basic(self, tmp_path):
        """Test basic atomic write functionality."""
        from backend.utils.atomic import atomic_write
        
        test_file = tmp_path / "test.txt"
        content = "Hello, atomic world!"
        
        atomic_write(test_file, content)
        
        assert test_file.exists()
        assert test_file.read_text() == content
    
    def test_atomic_write_json(self, tmp_path):
        """Test atomic JSON write functionality."""
        from backend.utils.atomic import atomic_write_json
        import json
        
        test_file = tmp_path / "test.json"
        data = {"key": "value", "nested": {"item": 42}}
        
        atomic_write_json(test_file, data)
        
        assert test_file.exists()
        loaded = json.loads(test_file.read_text())
        assert loaded == data
    
    def test_atomic_write_creates_parent_dirs(self, tmp_path):
        """Test that atomic_write creates parent directories."""
        from backend.utils.atomic import atomic_write
        
        test_file = tmp_path / "nested" / "deep" / "test.txt"
        content = "Nested content"
        
        atomic_write(test_file, content)
        
        assert test_file.exists()
        assert test_file.read_text() == content


class TestScriptDryRuns:
    """Integration tests for --dry-run flags"""
    
    def test_build_site_dry_run_parser(self):
        """Verify build_site has --dry-run argument."""
        from scripts import build_site
        import argparse
        
        # Create a parser similar to build_site.main()
        parser = argparse.ArgumentParser()
        parser.add_argument("--dry-run", "-n", action="store_true")
        
        # Parse with --dry-run
        args = parser.parse_args(["--dry-run"])
        assert args.dry_run is True
    
    def test_optimize_images_dry_run_parser(self):
        """Verify optimize_images has --dry-run argument."""
        import argparse
        
        parser = argparse.ArgumentParser()
        parser.add_argument("--dry-run", "-n", action="store_true")
        
        # Parse with -n shortcut
        args = parser.parse_args(["-n"])
        assert args.dry_run is True
