"""
Core package for AppData to Program Files Migrator.
"""
from .models import InstalledApp, MigrationOptions, MigrationResult, ShortcutInfo
from .detector import scan_for_installed_apps
from .migrator import Migrator
from .rollback import RollbackManager
from .elevation import is_admin, request_admin

__all__ = [
    "InstalledApp",
    "MigrationOptions",
    "MigrationResult",
    "ShortcutInfo",
    "scan_for_installed_apps",
    "Migrator",
    "RollbackManager",
    "is_admin",
    "request_admin",
]
