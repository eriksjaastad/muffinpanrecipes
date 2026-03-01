"""Admin dashboard API and routes."""

__all__ = ["create_admin_app"]


def create_admin_app(*args, **kwargs):
    """Lazy import to avoid circular dependency."""
    from backend.admin.app import create_admin_app as _create
    return _create(*args, **kwargs)
