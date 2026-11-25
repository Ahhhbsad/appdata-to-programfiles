"""
Core migration engine: orchestrates process termination, file transfer,
NTFS permissions, directory junctions, registry replication, shortcuts, and PATH.
"""
import os
import sys
import shutil
import subprocess
import time
import json
import winreg
from datetime import datetime
from typing import List, Optional, Callable, Dict, Any
from pathlib import Path

from .models import (
    InstalledApp,
    MigrationOptions,
    MigrationResult,
    MigrationStepRecord,
)
from .elevation import is_admin
from .junction import create_junction, remove_junction, is_reparse_point
from .registry import (
    export_key_to_reg,
    copy_registry_tree_with_replacement,
    delete_registry_tree,
    setup_app_paths,
    migrate_classes_associations,
    UNINSTALL_SUBKEY,
)
from .shortcuts import update_shortcut_paths, migrate_shortcut_to_common
from .env import update_path_variable


def get_backup_root() -> str:
    """Returns the central directory for migration backups and manifests."""
    program_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
    backup_dir = os.path.join(program_data, "AppDataMigrator", "Backups")
    try:
        os.makedirs(backup_dir, exist_ok=True)
        return backup_dir
    except (PermissionError, OSError):
        # Fallback to user-writable LocalAppData when running non-elevated
        local_app_data = os.environ.get("LOCALAPPDATA", os.path.expanduser(r"~\AppData\Local"))
        fallback_dir = os.path.join(local_app_data, "AppDataMigrator", "Backups")
        os.makedirs(fallback_dir, exist_ok=True)
        return fallback_dir


class Migrator:
    """Manages the migration of a user-installed app from AppData to Program Files."""

    def __init__(self, log_callback: Optional[Callable[[str], None]] = None):
        self.log_callback = log_callback or (lambda msg: print(msg))

    def _log(self, msg: str):
        self.log_callback(msg)

    def terminate_processes(self, pids: List[int]) -> bool:
        """Terminates running processes belonging to the application."""
        for pid in pids:
            self._log(f"Terminating running process PID {pid}...")
            try:
                subprocess.run(
                    ["taskkill.exe", "/F", "/PID", str(pid)],
                    capture_output=True,
                    timeout=5
                )
            except Exception as e:
                self._log(f"Warning: Failed to terminate PID {pid}: {e}")
        time.sleep(1)
        return True

    def fix_ntfs_permissions(self, target_dir: str):
        """Grants Read & Execute permissions to BUILTIN\\Users for standard user access."""
        self._log(f"Configuring NTFS permissions for BUILTIN\\Users on {target_dir}...")
        cmd = f'icacls.exe "{target_dir}" /grant *S-1-5-32-545:(OI)(CI)RX /T /C /Q'
        try:
            subprocess.run(cmd, shell=True, capture_output=True, timeout=30)
        except Exception as e:
            self._log(f"Warning: icacls permissions note: {e}")

    def copy_directory_robocopy(self, src: str, dst: str) -> bool:
        """
        Copies directory tree using robocopy for maximum Windows compatibility and speed.
        Robocopy return codes 0-7 indicate success/copied files.
        """
        os.makedirs(dst, exist_ok=True)
        cmd = [
            "robocopy.exe",
            src,
            dst,
            "/E",           # copy subdirectories, including empty ones
            "/COPY:DAT",    # copy Data, Attributes, Timestamps
            "/DCOPY:T",     # copy Directory Timestamps
            "/R:2",         # 2 retries on error
            "/W:1",         # 1 second wait between retries
            "/XJ",          # exclude junction points inside source
            "/NFL",         # no file logging for speed
            "/NDL",         # no directory logging
            "/NP",          # no progress percentage
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        # In robocopy, exit codes < 8 mean success (0 = no files copied, 1 = files copied, etc.)
        if proc.returncode < 8:
            return True
        self._log(f"Robocopy failed with return code {proc.returncode}: {proc.stderr}")
        return False

    def migrate(self, app: InstalledApp, options: MigrationOptions) -> MigrationResult:
        """Executes full migration pipeline for the given application."""
        steps: List[MigrationStepRecord] = []
        app_name = app.display_name or app.name
        source_dir = os.path.normpath(app.source_path)
        target_dir = os.path.normpath(options.target_program_files_custom or app.target_path)

        self._log(f"\n==================================================")
        self._log(f"Starting Migration: {app_name}")
        self._log(f"Source: {source_dir}")
        self._log(f"Target: {target_dir}")
        self._log(f"==================================================")

        # Dry run check
        if options.dry_run:
            self._log("[DRY RUN MODE] Simulating steps without making persistent changes...")
            steps.append(MigrationStepRecord("DryRun", "INFO", f"Would migrate {source_dir} to {target_dir}"))
            if app.registry_key_name:
                steps.append(MigrationStepRecord(
                    "Registry", "INFO",
                    f"Would copy HKCU\\...\\{app.registry_key_name} to HKLM\\...\\{app.registry_key_name}"
                ))
            if options.create_junction:
                steps.append(MigrationStepRecord(
                    "Junction", "INFO", f"Would create junction at {source_dir} -> {target_dir}"
                ))
            steps.append(MigrationStepRecord("Shortcuts", "INFO", f"Would update {len(app.shortcuts)} shortcuts"))
            steps.append(MigrationStepRecord("Environment", "INFO", f"Would update {len(app.path_entries)} PATH entries"))
            return MigrationResult(
                app_name=app_name,
                success=True,
                source_path=source_dir,
                target_path=target_dir,
                steps=steps
            )

        # 1. Administrator Check
        if not is_admin():
            err = "Administrator privileges are required to write to Program Files and HKLM."
            self._log(f"ERROR: {err}")
            return MigrationResult(
                app_name=app_name,
                success=False,
                source_path=source_dir,
                target_path=target_dir,
                error_message=err
            )

        # 2. Check source existence
        if not os.path.exists(source_dir):
            err = f"Source directory does not exist: {source_dir}"
            self._log(f"ERROR: {err}")
            return MigrationResult(
                app_name=app_name,
                success=False,
                source_path=source_dir,
                target_path=target_dir,
                error_message=err
            )

        # 3. Handle Running Processes
        if app.running_pids:
            if options.kill_running_processes:
                self.terminate_processes(app.running_pids)
                steps.append(MigrationStepRecord("Processes", "SUCCESS", f"Terminated {len(app.running_pids)} processes"))
            else:
                err = f"Application is currently running ({len(app.running_pids)} active processes). Please close it first."
                self._log(f"ERROR: {err}")
                return MigrationResult(
                    app_name=app_name,
                    success=False,
                    source_path=source_dir,
                    target_path=target_dir,
                    error_message=err
                )

        # 4. Create Backup & Manifest Directory
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_id = f"{app.name}_{timestamp}"
        backup_dir = os.path.join(get_backup_root(), backup_id)
        os.makedirs(backup_dir, exist_ok=True)
        manifest_file = os.path.join(backup_dir, "manifest.json")
        reg_backup_file = os.path.join(backup_dir, "registry_hkcu.reg")

        manifest_data: Dict[str, Any] = {
            "backup_id": backup_id,
            "app_name": app_name,
            "folder_name": app.name,
            "source_path": source_dir,
            "target_path": target_dir,
            "timestamp": timestamp,
            "registry_key_name": app.registry_key_name,
            "main_exe": app.main_exe,
            "junction_created": False,
            "shortcuts": [
                {
                    "lnk_path": s.lnk_path,
                    "original_target": s.target_path,
                    "arguments": s.arguments,
                    "working_dir": s.working_dir,
                    "icon": s.icon_location,
                }
                for s in app.shortcuts
            ],
            "path_entries": app.path_entries,
            "options": {
                "create_junction": options.create_junction,
                "migrate_to_all_users_shortcuts": options.migrate_to_all_users_shortcuts,
                "update_system_path": options.update_system_path,
            }
        }

        # Backup HKCU registry if key is known
        if app.registry_key_name:
            full_sub = f"{UNINSTALL_SUBKEY}\\{app.registry_key_name}"
            ok_exp, exp_msg = export_key_to_reg("HKCU", full_sub, reg_backup_file)
            if ok_exp:
                manifest_data["reg_backup_file"] = reg_backup_file
                steps.append(MigrationStepRecord("RegistryBackup", "SUCCESS", f"Exported HKCU key to {reg_backup_file}"))
                self._log(f"Backed up HKCU registry key: {full_sub}")
            else:
                steps.append(MigrationStepRecord("RegistryBackup", "WARNING", f"Registry export warning: {exp_msg}"))

        # 5. File Transfer
        self._log("Transferring files to Program Files...")
        if os.path.exists(target_dir):
            self._log(f"Target folder {target_dir} already exists. Merging/overwriting files...")

        copied_ok = self.copy_directory_robocopy(source_dir, target_dir)
        if not copied_ok:
            # Fallback to python shutil
            try:
                shutil.copytree(source_dir, target_dir, dirs_exist_ok=True)
                copied_ok = True
            except Exception as e:
                err = f"Failed copying files to destination: {e}"
                self._log(f"ERROR: {err}")
                return MigrationResult(
                    app_name=app_name,
                    success=False,
                    source_path=source_dir,
                    target_path=target_dir,
                    error_message=err,
                    steps=steps
                )

        steps.append(MigrationStepRecord("FilesCopy", "SUCCESS", f"Copied files to {target_dir}"))
        self._log("Files copied successfully.")

        # Fix permissions on target directory
        self.fix_ntfs_permissions(target_dir)

        # 6. Replace source with Junction or clean up
        temp_renamed_source = source_dir + f".migrated_{timestamp}"
        try:
            # Rename source to temporary location before junction creation
            self._log(f"Moving original folder to temp location: {temp_renamed_source}")
            os.rename(source_dir, temp_renamed_source)
            manifest_data["temp_renamed_source"] = temp_renamed_source

            if options.create_junction:
                self._log(f"Creating NTFS directory junction at original path...")
                j_ok, j_msg = create_junction(source_dir, target_dir)
                if j_ok:
                    manifest_data["junction_created"] = True
                    steps.append(MigrationStepRecord("Junction", "SUCCESS", j_msg))
                    self._log(f"Junction established: {source_dir} -> {target_dir}")
                else:
                    steps.append(MigrationStepRecord("Junction", "FAILED", j_msg))
                    self._log(f"Warning: Junction creation failed: {j_msg}")

            # Once junction is in place (or if no junction requested), safely purge temp_renamed_source
            self._log("Purging temp original files...")
            try:
                shutil.rmtree(temp_renamed_source, ignore_errors=True)
                steps.append(MigrationStepRecord("CleanupOriginal", "SUCCESS", "Removed original AppData files"))
            except Exception as e:
                self._log(f"Note: Could not immediately remove temp folder {temp_renamed_source}: {e}")

        except Exception as e:
            err = f"Failed swapping original folder for junction: {e}"
            self._log(f"ERROR: {err}")
            steps.append(MigrationStepRecord("Junction", "FAILED", err))
            # Restore if possible
            if os.path.exists(temp_renamed_source) and not os.path.exists(source_dir):
                try:
                    os.rename(temp_renamed_source, source_dir)
                except Exception:
                    pass

        # 7. Migrate Registry
        if app.registry_key_name:
            self._log(f"Replicating registry key {app.registry_key_name} from HKCU to HKLM...")
            sub = f"{UNINSTALL_SUBKEY}\\{app.registry_key_name}"
            reg_ok, reg_logs = copy_registry_tree_with_replacement(
                winreg.HKEY_CURRENT_USER, sub,
                winreg.HKEY_LOCAL_MACHINE, sub,
                source_dir, target_dir
            )
            for l in reg_logs:
                self._log(f" - {l}")

            if reg_ok:
                steps.append(MigrationStepRecord("RegistryCopy", "SUCCESS", f"Copied to HKLM\\{sub}"))
                # Delete original HKCU uninstall key to prevent duplicate listing in Settings
                del_ok, del_msg = delete_registry_tree(winreg.HKEY_CURRENT_USER, sub)
                if del_ok:
                    self._log(f"Cleaned up original HKCU uninstall key.")
                    steps.append(MigrationStepRecord("RegistryCleanup", "SUCCESS", "Removed HKCU uninstall entry"))
                else:
                    self._log(f"Note: {del_msg}")
            else:
                steps.append(MigrationStepRecord("RegistryCopy", "FAILED", "Failed copying to HKLM"))

        # Setup App Paths (Win+R / CMD launcher)
        if app.main_exe:
            exe_fname = os.path.basename(app.main_exe)
            target_main_exe = os.path.join(target_dir, os.path.relpath(app.main_exe, source_dir))
            if os.path.exists(target_main_exe):
                ap_ok, ap_msg = setup_app_paths(exe_fname, target_main_exe, target_dir)
                if ap_ok:
                    self._log(f"Configured global App Paths for {exe_fname}")
                    steps.append(MigrationStepRecord("AppPaths", "SUCCESS", ap_msg))

        # Migrate Classes / Protocol handlers
        class_logs = migrate_classes_associations(source_dir, target_dir)
        for cl in class_logs:
            self._log(f" - {cl}")
        if class_logs:
            steps.append(MigrationStepRecord("FileAssociations", "SUCCESS", f"Updated {len(class_logs)} protocol/file associations"))

        # 8. Migrate Shortcuts (.lnk)
        for sc in app.shortcuts:
            self._log(f"Updating shortcut: {sc.lnk_path}...")
            up_ok, up_msg = update_shortcut_paths(sc.lnk_path, source_dir, target_dir)
            if up_ok:
                steps.append(MigrationStepRecord("ShortcutUpdate", "SUCCESS", up_msg))
            else:
                steps.append(MigrationStepRecord("ShortcutUpdate", "WARNING", up_msg))

            if options.migrate_to_all_users_shortcuts and (sc.is_user_desktop or sc.is_user_start_menu):
                m_ok, m_msg = migrate_shortcut_to_common(sc.lnk_path, sc.is_user_desktop, sc.is_user_start_menu)
                if m_ok:
                    self._log(f"Copied shortcut to All Users: {m_msg}")
                    steps.append(MigrationStepRecord("ShortcutCommon", "SUCCESS", m_msg))

        # 9. Update PATH Environment Variable
        if app.path_entries:
            self._log("Updating PATH environment variables...")
            p_ok, p_logs = update_path_variable(
                source_dir,
                target_dir,
                promote_to_system=options.update_system_path
            )
            for pl in p_logs:
                self._log(f" - {pl}")
            steps.append(MigrationStepRecord("EnvironmentPATH", "SUCCESS", f"Updated PATH entries: {len(app.path_entries)}"))

        # 10. Save Final Manifest
        manifest_data["steps"] = [
            {"step": s.step, "status": s.status, "details": s.details} for s in steps
        ]
        manifest_data["completed_at"] = datetime.now().isoformat()
        try:
            with open(manifest_file, "w", encoding="utf-8") as f:
                json.dump(manifest_data, f, indent=2)
            self._log(f"Saved migration manifest to {manifest_file}")
        except Exception as e:
            self._log(f"Warning: Could not save manifest file: {e}")

        self._log(f"\nMigration completed successfully for {app_name}!")
        return MigrationResult(
            app_name=app_name,
            success=True,
            source_path=source_dir,
            target_path=target_dir,
            steps=steps,
            backup_id=backup_id,
            manifest_path=manifest_file
        )
