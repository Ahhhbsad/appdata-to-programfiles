"""
Environment variable (PATH) detection, path rewriting, and notification broadcast.
"""
import os
import sys
import winreg
import ctypes
from typing import List, Tuple, Dict, Any


HWND_BROADCAST = 0xFFFF
WM_SETTINGCHANGE = 0x001A
SMTO_ABORTIFHUNG = 0x0002


def broadcast_environment_change():
    """
    Broadcasts WM_SETTINGCHANGE to notify all top-level windows of environment variable updates.
    """
    try:
        result = ctypes.c_ulong()
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST,
            WM_SETTINGCHANGE,
            0,
            "Environment",
            SMTO_ABORTIFHUNG,
            5000,
            ctypes.byref(result)
        )
    except Exception as e:
        print(f"Failed to broadcast WM_SETTINGCHANGE: {e}", file=sys.stderr)


def get_user_path() -> List[str]:
    """Retrieve entries in the User PATH environment variable from registry."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, "PATH")
            return [p.strip() for p in val.split(";") if p.strip()]
    except Exception:
        return []


def get_system_path() -> List[str]:
    """Retrieve entries in the System PATH environment variable from registry."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
            0,
            winreg.KEY_READ
        ) as key:
            val, _ = winreg.QueryValueEx(key, "PATH")
            return [p.strip() for p in val.split(";") if p.strip()]
    except Exception:
        return []


def find_path_entries_for_app(app_dir: str) -> List[str]:
    """Find any User or System PATH entries that reside inside app_dir."""
    app_dir_norm = os.path.normpath(app_dir).lower()
    matches = []
    for p in get_user_path() + get_system_path():
        p_norm = os.path.normpath(p).lower()
        if p_norm == app_dir_norm or p_norm.startswith(app_dir_norm + os.sep):
            if p not in matches:
                matches.append(p)
    return matches


def update_path_variable(
    old_dir: str,
    new_dir: str,
    promote_to_system: bool = True
) -> Tuple[bool, List[str]]:
    """
    Replaces occurrences of old_dir with new_dir in PATH.
    Optionally promotes user PATH entries to System PATH.
    """
    old_dir_norm = os.path.normpath(old_dir).lower()
    changes_made = []

    # 1. Update User PATH
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_ALL_ACCESS) as key:
            try:
                user_val, val_type = winreg.QueryValueEx(key, "PATH")
            except FileNotFoundError:
                user_val, val_type = "", winreg.REG_EXPAND_SZ

            user_paths = [p.strip() for p in user_val.split(";") if p.strip()]
            new_user_paths = []
            promoted_paths = []

            for p in user_paths:
                p_norm = os.path.normpath(p).lower()
                if p_norm == old_dir_norm or p_norm.startswith(old_dir_norm + os.sep):
                    rel = os.path.relpath(p, old_dir)
                    updated_p = os.path.normpath(os.path.join(new_dir, rel))
                    if promote_to_system:
                        promoted_paths.append(updated_p)
                        changes_made.append(f"Moved User PATH entry to System: {updated_p}")
                    else:
                        new_user_paths.append(updated_p)
                        changes_made.append(f"Updated User PATH entry: {p} -> {updated_p}")
                else:
                    new_user_paths.append(p)

            new_val = ";".join(new_user_paths)
            winreg.SetValueEx(key, "PATH", 0, val_type, new_val)
    except Exception as e:
        changes_made.append(f"Warning: Could not update HKCU Environment: {e}")

    # 2. Update System PATH
    if promote_to_system or True:
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
                0,
                winreg.KEY_ALL_ACCESS
            ) as sys_key:
                try:
                    sys_val, sys_type = winreg.QueryValueEx(sys_key, "PATH")
                except FileNotFoundError:
                    sys_val, sys_type = "", winreg.REG_EXPAND_SZ

                sys_paths = [p.strip() for p in sys_val.split(";") if p.strip()]
                new_sys_paths = []
                for p in sys_paths:
                    p_norm = os.path.normpath(p).lower()
                    if p_norm == old_dir_norm or p_norm.startswith(old_dir_norm + os.sep):
                        rel = os.path.relpath(p, old_dir)
                        updated_p = os.path.normpath(os.path.join(new_dir, rel))
                        new_sys_paths.append(updated_p)
                        changes_made.append(f"Updated System PATH entry: {p} -> {updated_p}")
                    else:
                        new_sys_paths.append(p)

                # Add any paths promoted from User PATH
                for p in promoted_paths:
                    if p not in new_sys_paths:
                        new_sys_paths.append(p)

                winreg.SetValueEx(sys_key, "PATH", 0, sys_type, ";".join(new_sys_paths))
        except Exception as e:
            changes_made.append(f"Warning: Could not update HKLM Environment (Admin required?): {e}")

    # Broadcast notification to the operating system
    broadcast_environment_change()

    return True, changes_made
