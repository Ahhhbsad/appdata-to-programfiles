"""
Data models for AppData to Program Files Migrator.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from pathlib import Path


@dataclass
class ShortcutInfo:
    """Represents a Windows shortcut (.lnk file) referencing an app."""
    lnk_path: str
    target_path: str
    arguments: str = ""
    working_dir: str = ""
    icon_location: str = ""
    is_user_desktop: bool = False
    is_user_start_menu: bool = False


@dataclass
class InstalledApp:
    """Represents an application installed in AppData."""
    name: str
    display_name: str
    source_path: str
    target_path: str
    publisher: str = "Unknown"
    version: str = "Unknown"
    install_location: str = ""
    display_icon: str = ""
    uninstall_string: str = ""
    registry_key_name: str = ""
    registry_hive: str = "HKCU"  # HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall
    size_bytes: int = 0
    is_running: bool = False
    running_pids: List[int] = field(default_factory=list)
    shortcuts: List[ShortcutInfo] = field(default_factory=list)
    path_entries: List[str] = field(default_factory=list)
    is_64bit: bool = True
    main_exe: str = ""

    @property
    def size_mb(self) -> float:
        return round(self.size_bytes / (1024 * 1024), 2)

    @property
    def size_gb(self) -> float:
        return round(self.size_bytes / (1024 * 1024 * 1024), 2)

    @property
    def formatted_size(self) -> str:
        if self.size_bytes >= 1024 * 1024 * 1024:
            return f"{self.size_gb} GB"
        elif self.size_bytes >= 1024 * 1024:
            return f"{self.size_mb} MB"
        elif self.size_bytes > 0:
            return f"{round(self.size_bytes / 1024, 1)} KB"
        return "0 KB"


@dataclass
class MigrationOptions:
    """User-selectable options for the migration operation."""
    create_junction: bool = True
    migrate_to_all_users_shortcuts: bool = True
    update_system_path: bool = True
    create_backup: bool = True
    kill_running_processes: bool = False
    target_program_files_custom: Optional[str] = None
    dry_run: bool = False


@dataclass
class MigrationStepRecord:
    step: str
    status: str  # SUCCESS, FAILED, SKIPPED, INFO
    details: str


@dataclass
class MigrationResult:
    """Result of an application migration."""
    app_name: str
    success: bool
    source_path: str
    target_path: str
    steps: List[MigrationStepRecord] = field(default_factory=list)
    error_message: Optional[str] = None
    backup_id: Optional[str] = None
    manifest_path: Optional[str] = None
