"""
Rollback engine: parses migration manifests and safely reverses migrations.
"""
import os
import sys
import json
import shutil
import winreg
import subprocess
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple, Callable

from .elevation import is_admin
from .junction import remove_junction, is_reparse_point
from .registry import (
    import_reg_file,
    delete_registry_tree,
    remove_app_paths,
    UNINSTALL_SUBKEY,
)
from .shortcuts import update_shortcut_paths, get_shortcut_directories
from .env import update_path_variable
from .migrator import get_backup_root


class RollbackManager:
    """Manages discovery and restoration of previous migrations."""

    def __init__(self, log_callback: Optional[Callable[[str], None]] = None):
        self.log_callback = log_callback or (lambda msg: print(msg))

    def _log(self, msg: str):
        self.log_callback(msg)

    def list_available_backups(self) -> List[Dict[str, Any]]:
        """Scans the backup directory for available migration manifests."""
        roots = []
        program_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        roots.append(os.path.join(program_data, "AppDataMigrator", "Backups"))
        local_app_data = os.environ.get("LOCALAPPDATA", os.path.expanduser(r"~\AppData\Local"))
        roots.append(os.path.join(local_app_data, "AppDataMigrator", "Backups"))

        backups = []
        seen_ids = set()

        for root in roots:
            if not os.path.exists(root):
                continue
            for item in sorted(os.listdir(root), reverse=True):
                if item in seen_ids:
                    continue
                item_dir = os.path.join(root, item)
                manifest_p = os.path.join(item_dir, "manifest.json")
                if os.path.isfile(manifest_p):
                    try:
                        with open(manifest_p, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            data["manifest_dir"] = item_dir
                            backups.append(data)
                            seen_ids.add(item)
                    except Exception:
                        pass
        return backups

    def rollback(self, backup_id: str) -> Tuple[bool, str]:
        """Reverses the migration identified by backup_id."""
        if not is_admin():
            return False, "Administrator privileges are required for rollback."

        backup_dir = os.path.join(get_backup_root(), backup_id)
        manifest_p = os.path.join(backup_dir, "manifest.json")
        if not os.path.exists(manifest_p):
            return False, f"Manifest not found for backup ID: {backup_id}"

        try:
            with open(manifest_p, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception as e:
            return False, f"Failed reading manifest: {e}"

        app_name = manifest.get("app_name", backup_id)
        source_path = manifest.get("source_path", "")
        target_path = manifest.get("target_path", "")
        reg_backup_file = manifest.get("reg_backup_file", "")
        reg_key_name = manifest.get("registry_key_name", "")
        main_exe = manifest.get("main_exe", "")

        self._log(f"\n==================================================")
        self._log(f"Starting Rollback for: {app_name}")
        self._log(f"Restoring to: {source_path}")
        self._log(f"From: {target_path}")
        self._log(f"==================================================")

        # 1. Remove Junction at source_path if it exists
        if os.path.exists(source_path) and is_reparse_point(source_path):
            self._log(f"Removing NTFS junction at {source_path}...")
            ok_j, msg_j = remove_junction(source_path)
            self._log(f" - {msg_j}")
        elif os.path.exists(source_path):
            self._log(f"Notice: Path already exists at {source_path}")

        # 2. Move files back from Program Files to source_path
        if os.path.exists(target_path):
            self._log(f"Restoring files from {target_path} to {source_path}...")
            try:
                os.makedirs(os.path.dirname(source_path), exist_ok=True)
                # Copy or move
                shutil.copytree(target_path, source_path, dirs_exist_ok=True)
                shutil.rmtree(target_path, ignore_errors=True)
                self._log("Files restored successfully to AppData.")
            except Exception as e:
                self._log(f"Warning during file restore: {e}")

        # 3. Restore HKCU Registry and Clean HKLM
        if reg_backup_file and os.path.exists(reg_backup_file):
            self._log(f"Restoring HKCU registry from {reg_backup_file}...")
            imp_ok, imp_msg = import_reg_file(reg_backup_file)
            self._log(f" - {imp_msg}")

        if reg_key_name:
            sub = f"{UNINSTALL_SUBKEY}\\{reg_key_name}"
            self._log(f"Removing HKLM uninstall entry HKLM\\{sub}...")
            del_ok, del_msg = delete_registry_tree(winreg.HKEY_LOCAL_MACHINE, sub)
            self._log(f" - {del_msg}")

        # 4. Remove App Paths entry
        if main_exe:
            exe_name = os.path.basename(main_exe)
            self._log(f"Cleaning up App Paths for {exe_name}...")
            ap_ok, ap_msg = remove_app_paths(exe_name)
            self._log(f" - {ap_msg}")

        # 5. Restore Shortcuts
        shortcuts = manifest.get("shortcuts", [])
        for sc in shortcuts:
            lnk = sc.get("lnk_path", "")
            if os.path.exists(lnk):
                self._log(f"Reverting shortcut target: {lnk}...")
                update_shortcut_paths(lnk, target_path, source_path)

        # 6. Revert PATH entries
        path_entries = manifest.get("path_entries", [])
        if path_entries:
            self._log("Reverting PATH environment variables...")
            update_path_variable(target_path, source_path, promote_to_system=False)

        # Mark manifest as rolled back
        manifest["rolled_back_at"] = datetime.now().isoformat()
        try:
            with open(manifest_p, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2)
        except Exception:
            pass

        self._log(f"Rollback completed successfully for {app_name}.")
        return True, f"Rollback completed successfully for {app_name}."
