"""
Registry inspection, backup, transfer (HKCU -> HKLM), and path rewriting.
"""
import os
import sys
import winreg
import subprocess
from typing import Dict, Any, List, Tuple, Optional


UNINSTALL_SUBKEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
APP_PATHS_SUBKEY = r"Software\Microsoft\Windows\CurrentVersion\App Paths"


def export_key_to_reg(hive_str: str, key_path: str, output_file: str) -> Tuple[bool, str]:
    """
    Exports a registry key to a .reg file using Windows reg.exe.
    hive_str: 'HKCU' or 'HKLM'
    """
    full_key = f"{hive_str}\\{key_path}"
    os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
    cmd = f'reg.exe export "{full_key}" "{output_file}" /y'
    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
        if proc.returncode == 0:
            return True, f"Exported {full_key} to {output_file}"
        return False, f"reg export failed: {proc.stderr.strip()}"
    except Exception as e:
        return False, f"Exception exporting registry key: {e}"


def import_reg_file(reg_file: str) -> Tuple[bool, str]:
    """Imports a .reg file back into the Windows registry."""
    if not os.path.exists(reg_file):
        return False, f"File not found: {reg_file}"
    cmd = f'reg.exe import "{reg_file}"'
    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
        if proc.returncode == 0:
            return True, f"Successfully restored registry from {reg_file}"
        return False, f"reg import failed: {proc.stderr.strip()}"
    except Exception as e:
        return False, f"Exception importing registry file: {e}"


def _replace_in_value(val: Any, val_type: int, old_dir: str, new_dir: str) -> Any:
    """Replaces occurrences of old_dir with new_dir (case-insensitive) in strings."""
    if val_type in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) and isinstance(val, str):
        # Case-insensitive replacement preserving casing of replacement
        import re
        escaped_old = re.escape(old_dir)
        return re.sub(escaped_old, lambda m: new_dir, val, flags=re.IGNORECASE)
    elif val_type == winreg.REG_MULTI_SZ and isinstance(val, list):
        import re
        escaped_old = re.escape(old_dir)
        return [
            re.sub(escaped_old, lambda m: new_dir, item, flags=re.IGNORECASE) if isinstance(item, str) else item
            for item in val
        ]
    return val


def copy_registry_tree_with_replacement(
    src_hkey,
    src_subkey: str,
    dst_hkey,
    dst_subkey: str,
    old_dir: str,
    new_dir: str
) -> Tuple[bool, List[str]]:
    """
    Recursively copies a registry subkey from source hive to destination hive,
    replacing all occurrences of old_dir with new_dir in string values.
    """
    logs: List[str] = []

    try:
        src_key = winreg.OpenKey(src_hkey, src_subkey, 0, winreg.KEY_READ)
    except Exception as e:
        return False, [f"Could not open source key {src_subkey}: {e}"]

    try:
        dst_key = winreg.CreateKey(dst_hkey, dst_subkey)
    except Exception as e:
        winreg.CloseKey(src_key)
        return False, [f"Could not create destination key {dst_subkey} (Admin rights required): {e}"]

    try:
        # Copy values
        val_idx = 0
        while True:
            try:
                val_name, val_data, val_type = winreg.EnumValue(src_key, val_idx)
                val_idx += 1
                new_data = _replace_in_value(val_data, val_type, old_dir, new_dir)
                winreg.SetValueEx(dst_key, val_name, 0, val_type, new_data)
                if new_data != val_data:
                    logs.append(f"Updated registry value [{val_name}] with new path.")
            except WindowsError:
                break

        # Recursively copy subkeys
        subkey_idx = 0
        while True:
            try:
                sub_name = winreg.EnumKey(src_key, subkey_idx)
                subkey_idx += 1
                child_src = f"{src_subkey}\\{sub_name}"
                child_dst = f"{dst_subkey}\\{sub_name}"
                ok, sub_logs = copy_registry_tree_with_replacement(
                    src_hkey, child_src, dst_hkey, child_dst, old_dir, new_dir
                )
                logs.extend(sub_logs)
            except WindowsError:
                break

        return True, logs
    finally:
        winreg.CloseKey(src_key)
        winreg.CloseKey(dst_key)


def delete_registry_tree(hkey, subkey: str) -> Tuple[bool, str]:
    """Recursively deletes a registry subkey and all its descendants."""
    try:
        key = winreg.OpenKey(hkey, subkey, 0, winreg.KEY_ALL_ACCESS)
    except FileNotFoundError:
        return True, "Key does not exist."
    except Exception as e:
        return False, f"Could not open key for deletion: {e}"

    try:
        while True:
            try:
                sub_name = winreg.EnumKey(key, 0)
                delete_registry_tree(key, sub_name)
            except WindowsError:
                break
    finally:
        winreg.CloseKey(key)

    try:
        parent_path, leaf = os.path.split(subkey)
        if parent_path:
            with winreg.OpenKey(hkey, parent_path, 0, winreg.KEY_ALL_ACCESS) as parent:
                winreg.DeleteKey(parent, leaf)
        else:
            winreg.DeleteKey(hkey, leaf)
        return True, f"Deleted key {subkey}"
    except Exception as e:
        return False, f"Error deleting key {subkey}: {e}"


def setup_app_paths(exe_name: str, exe_full_path: str, app_dir: str) -> Tuple[bool, str]:
    """
    Registers the app in HKLM\\Software\\Microsoft\\Windows\\CurrentVersion\\App Paths\\<exe_name>
    so it can be invoked globally from Run (Win+R) or command prompt.
    """
    if not exe_name:
        return False, "No executable name provided."

    subkey = f"{APP_PATHS_SUBKEY}\\{exe_name}"
    try:
        with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, subkey) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, exe_full_path)
            winreg.SetValueEx(key, "Path", 0, winreg.REG_SZ, app_dir)
        return True, f"Registered App Path in HKLM for {exe_name}"
    except Exception as e:
        return False, f"Failed registering App Path for {exe_name}: {e}"


def remove_app_paths(exe_name: str) -> Tuple[bool, str]:
    """Removes an entry from App Paths in HKLM."""
    subkey = f"{APP_PATHS_SUBKEY}\\{exe_name}"
    return delete_registry_tree(winreg.HKEY_LOCAL_MACHINE, subkey)


def migrate_classes_associations(old_dir: str, new_dir: str) -> List[str]:
    """
    Scans HKCU\\Software\\Classes for URL protocols and file associations referencing old_dir,
    and updates them with new_dir or copies them to HKLM\\Software\\Classes.
    """
    logs = []
    classes_path = r"Software\Classes"

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, classes_path, 0, winreg.KEY_READ) as classes_key:
            idx = 0
            while True:
                try:
                    sub_name = winreg.EnumKey(classes_key, idx)
                    idx += 1
                    # Inspect shell\open\command
                    cmd_path = f"{classes_path}\\{sub_name}\\shell\\open\\command"
                    try:
                        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, cmd_path, 0, winreg.KEY_ALL_ACCESS) as cmd_key:
                            val, val_type = winreg.QueryValueEx(cmd_key, "")
                            if isinstance(val, str) and old_dir.lower() in val.lower():
                                new_val = _replace_in_value(val, val_type, old_dir, new_dir)
                                winreg.SetValueEx(cmd_key, "", 0, val_type, new_val)
                                logs.append(f"Updated HKCU association for '{sub_name}': {new_val}")
                                # Replicate to HKLM\\Software\\Classes
                                try:
                                    with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, cmd_path) as hklm_cmd:
                                        winreg.SetValueEx(hklm_cmd, "", 0, val_type, new_val)
                                        logs.append(f"Mirrored association to HKLM for '{sub_name}'")
                                except Exception:
                                    pass
                    except Exception:
                        pass
                except WindowsError:
                    break
    except Exception as e:
        logs.append(f"Classes scan note: {e}")

    return logs
