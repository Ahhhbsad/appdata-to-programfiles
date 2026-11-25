"""
Application discovery and inspection module.
Finds user-installed applications in AppData, inspects registry, processes, and shortcuts.
"""
import os
import sys
import winreg
import subprocess
import csv
from typing import List, Dict, Optional, Tuple, Set
from pathlib import Path

from .models import InstalledApp, ShortcutInfo
from .shortcuts import find_shortcuts_for_path, get_all_user_shortcuts
from .env import find_path_entries_for_app, get_user_path, get_system_path
from .junction import is_reparse_point, get_junction_target


def get_standard_paths() -> Dict[str, str]:
    """Return standard user and system paths."""
    user_profile = os.environ.get("USERPROFILE", "")
    local_app_data = os.environ.get("LOCALAPPDATA", os.path.join(user_profile, r"AppData\Local"))
    app_data = os.environ.get("APPDATA", os.path.join(user_profile, r"AppData\Roaming"))
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")

    return {
        "user_profile": user_profile,
        "local_app_data": local_app_data,
        "app_data": app_data,
        "local_programs": os.path.join(local_app_data, "Programs"),
        "program_files": program_files,
        "program_files_x86": program_files_x86,
    }


def get_running_process_paths() -> Dict[int, str]:
    """
    Returns a dictionary of {pid: executable_full_path} for currently running processes.
    Uses PowerShell or wmic/tasklist.
    """
    proc_map: Dict[int, str] = {}
    ps_cmd = 'Get-Process | Where-Object { $_.Path } | Select-Object Id, Path | ConvertTo-Csv -NoTypeInformation'
    try:
        res = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=10
        )
        if res.returncode == 0 and res.stdout:
            reader = csv.DictReader(res.stdout.splitlines())
            for row in reader:
                try:
                    pid = int(row.get("Id", 0))
                    path = row.get("Path", "").strip()
                    if pid and path:
                        proc_map[pid] = path
                except Exception:
                    pass
    except Exception:
        pass
    return proc_map


def calculate_folder_size(folder_path: str) -> int:
    """Calculates total size of files inside folder_path in bytes, ignoring junctions."""
    if is_reparse_point(folder_path):
        return 0
    total = 0
    try:
        for entry in os.scandir(folder_path):
            try:
                if entry.is_symlink() or is_reparse_point(entry.path):
                    continue
                if entry.is_file():
                    total += entry.stat().st_size
                elif entry.is_dir():
                    total += calculate_folder_size(entry.path)
            except (PermissionError, OSError):
                continue
    except (PermissionError, OSError):
        pass
    return total


def find_main_executable(folder_path: str, hint_name: str = "") -> str:
    """Finds the most likely primary executable in an application directory."""
    candidates = []
    try:
        for root, dirs, files in os.walk(folder_path):
            # Prioritize root level executables
            is_root = (root == folder_path)
            for f in files:
                if f.lower().endswith(".exe"):
                    full_p = os.path.join(root, f)
                    candidates.append((full_p, is_root, f.lower()))
    except Exception:
        return ""

    if not candidates:
        return ""

    hint_lower = hint_name.lower().replace(" ", "")

    # 1. Exact match with folder/app name at root level
    for full_p, is_root, fname in candidates:
        if is_root and fname.replace(".exe", "") == hint_lower:
            return full_p

    # 2. Contains match at root level
    for full_p, is_root, fname in candidates:
        if is_root and (hint_lower in fname or fname.replace(".exe", "") in hint_lower):
            if "uninstall" not in fname and "update" not in fname:
                return full_p

    # 3. Any root-level non-installer exe
    for full_p, is_root, fname in candidates:
        if is_root and "uninstall" not in fname and "update" not in fname and "helper" not in fname:
            return full_p

    # 4. Fallback to first non-installer exe
    for full_p, _, fname in candidates:
        if "uninstall" not in fname:
            return full_p

    return candidates[0][0]


def is_binary_64bit(exe_path: str) -> bool:
    """Inspects PE header to check if binary is 64-bit."""
    try:
        with open(exe_path, "rb") as f:
            header = f.read(1024)
            if len(header) < 64:
                return True
            # e_lfanew is at offset 0x3C
            pe_offset = int.from_bytes(header[0x3C:0x40], byteorder="little")
            if len(header) < pe_offset + 6:
                f.seek(pe_offset)
                pe_sig = f.read(6)
            else:
                pe_sig = header[pe_offset:pe_offset+6]
            # PE signature 'PE\0\0' (0x50, 0x45, 0x00, 0x00)
            if pe_sig[:4] == b"PE\x00\x00":
                machine = int.from_bytes(pe_sig[4:6], byteorder="little")
                # 0x8664 is AMD64 / x64, 0x014c is i386
                if machine == 0x8664:
                    return True
                elif machine == 0x014c:
                    return False
    except Exception:
        pass
    return True


def query_hkcu_uninstall_apps() -> List[Dict[str, Any]]:
    """Reads all uninstall entries under HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall."""
    uninstall_apps = []
    base_subkey = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"

    # Also check Wow6432Node if present
    hives_to_check = [
        (base_subkey, winreg.KEY_READ),
    ]
    if hasattr(winreg, "KEY_WOW64_64KEY"):
        hives_to_check.append((base_subkey, winreg.KEY_READ | winreg.KEY_WOW64_64KEY))

    visited_keys = set()

    for subkey, flags in hives_to_check:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey, 0, flags) as root_key:
                idx = 0
                while True:
                    try:
                        app_key_name = winreg.EnumKey(root_key, idx)
                        idx += 1
                        if app_key_name in visited_keys:
                            continue
                        visited_keys.add(app_key_name)

                        app_info = {"KeyName": app_key_name}
                        try:
                            with winreg.OpenKey(root_key, app_key_name) as app_key:
                                v_idx = 0
                                while True:
                                    try:
                                        v_name, v_data, _ = winreg.EnumValue(app_key, v_idx)
                                        v_idx += 1
                                        app_info[v_name] = v_data
                                    except WindowsError:
                                        break
                                uninstall_apps.append(app_info)
                        except Exception:
                            continue
                    except WindowsError:
                        break
        except Exception:
            continue

    return uninstall_apps


def scan_for_installed_apps() -> List[InstalledApp]:
    """
    Comprehensive scanner for user-installed applications across AppData and HKCU Registry.
    """
    paths = get_standard_paths()
    local_app_data = paths["local_app_data"].lower()
    app_data = paths["app_data"].lower()
    user_profile = paths["user_profile"].lower()
    local_programs = paths["local_programs"]
    prog_files = paths["program_files"]
    prog_files_x86 = paths["program_files_x86"]

    running_procs = get_running_process_paths()
    cached_shortcuts = get_all_user_shortcuts()
    all_path_entries = list(dict.fromkeys(get_user_path() + get_system_path()))

    def _match_paths(app_dir: str) -> List[str]:
        norm = os.path.normpath(app_dir).lower()
        return [
            p for p in all_path_entries
            if os.path.normpath(p).lower() == norm or os.path.normpath(p).lower().startswith(norm + os.sep)
        ]

    excluded_roots = {
        local_app_data,
        app_data,
        user_profile,
        os.path.normpath(local_programs).lower(),
        os.path.join(local_app_data, "temp"),
        os.path.join(local_app_data, "packages"),
        os.path.join(local_app_data, "microsoft"),
        os.path.join(app_data, "microsoft"),
    }

    installed_apps: List[InstalledApp] = []
    tracked_source_dirs: Set[str] = set()

    # 1. Query Registry Uninstall entries first
    reg_entries = query_hkcu_uninstall_apps()

    for entry in reg_entries:
        key_name = entry.get("KeyName", "")
        disp_name = entry.get("DisplayName", key_name)
        install_loc = entry.get("InstallLocation", "")
        disp_icon = entry.get("DisplayIcon", "")
        uninstall_str = entry.get("UninstallString", "")
        publisher = entry.get("Publisher", "Unknown")
        version = entry.get("DisplayVersion", "Unknown")

        source_dir = ""
        if install_loc and os.path.exists(install_loc):
            source_dir = install_loc
        elif disp_icon:
            clean_icon = disp_icon.split(",")[0].strip('"')
            if os.path.exists(clean_icon):
                source_dir = os.path.dirname(clean_icon)
        elif uninstall_str:
            clean_uninst = uninstall_str.strip('"').split(" ")[0].strip('"')
            if os.path.exists(clean_uninst):
                source_dir = os.path.dirname(clean_uninst)

        # Verify that source_dir is indeed inside AppData / UserProfile
        if not source_dir or not os.path.isdir(source_dir):
            continue

        source_dir_norm = os.path.normpath(source_dir)
        source_dir_lower = source_dir_norm.lower()

        is_in_appdata = (
            source_dir_lower.startswith(local_app_data) or
            source_dir_lower.startswith(app_data) or
            (source_dir_lower.startswith(user_profile) and "program" in source_dir_lower)
        )

        if not is_in_appdata or source_dir_lower in excluded_roots:
            continue

        # Ensure it's not a Temp/Packages subfolder
        if source_dir_lower.startswith(os.path.join(local_app_data, "temp") + os.sep):
            continue
        if source_dir_lower.startswith(os.path.join(local_app_data, "packages") + os.sep):
            continue

        # Skip if already a junction pointing to Program Files
        if is_reparse_point(source_dir_norm):
            continue

        if source_dir_lower in tracked_source_dirs:
            continue
        tracked_source_dirs.add(source_dir_lower)

        # Determine target folder name and path
        folder_name = os.path.basename(source_dir_norm)
        main_exe = find_main_executable(source_dir_norm, disp_name)
        is_64 = is_binary_64bit(main_exe) if main_exe else True
        target_base = prog_files if is_64 else prog_files_x86
        target_path = os.path.join(target_base, folder_name)

        # Check running processes
        running_pids = []
        for pid, exe_p in running_procs.items():
            if os.path.normpath(exe_p).lower().startswith(source_dir_lower):
                running_pids.append(pid)

        app_obj = InstalledApp(
            name=folder_name,
            display_name=disp_name,
            source_path=source_dir_norm,
            target_path=target_path,
            publisher=publisher,
            version=version,
            install_location=install_loc,
            display_icon=disp_icon,
            uninstall_string=uninstall_str,
            registry_key_name=key_name,
            registry_hive="HKCU",
            size_bytes=calculate_folder_size(source_dir_norm),
            is_running=bool(running_pids),
            running_pids=running_pids,
            shortcuts=find_shortcuts_for_path(source_dir_norm, cached_shortcuts),
            path_entries=_match_paths(source_dir_norm),
            is_64bit=is_64,
            main_exe=main_exe,
        )
        installed_apps.append(app_obj)

    # 2. Scan LocalAppData\Programs for any applications without explicit HKCU uninstall keys
    if os.path.exists(local_programs):
        try:
            for item in os.listdir(local_programs):
                item_full = os.path.join(local_programs, item)
                item_lower = os.path.normpath(item_full).lower()
                if not os.path.isdir(item_full) or is_reparse_point(item_full):
                    continue
                if item_lower in tracked_source_dirs:
                    continue
                tracked_source_dirs.add(item_lower)

                main_exe = find_main_executable(item_full, item)
                is_64 = is_binary_64bit(main_exe) if main_exe else True
                target_base = prog_files if is_64 else prog_files_x86
                target_path = os.path.join(target_base, item)

                running_pids = []
                for pid, exe_p in running_procs.items():
                    if os.path.normpath(exe_p).lower().startswith(item_lower):
                        running_pids.append(pid)

                app_obj = InstalledApp(
                    name=item,
                    display_name=item,
                    source_path=os.path.normpath(item_full),
                    target_path=target_path,
                    size_bytes=calculate_folder_size(item_full),
                    is_running=bool(running_pids),
                    running_pids=running_pids,
                    shortcuts=find_shortcuts_for_path(item_full, cached_shortcuts),
                    path_entries=_match_paths(item_full),
                    is_64bit=is_64,
                    main_exe=main_exe,
                )
                installed_apps.append(app_obj)
        except Exception:
            pass

    # 3. Scan common standalone per-user app folders in LocalAppData and Roaming
    known_user_locations = [
        os.path.join(paths["local_app_data"], "Discord"),
        os.path.join(paths["local_app_data"], "slack"),
        os.path.join(paths["local_app_data"], "Postman"),
        os.path.join(paths["local_app_data"], "GitHubDesktop"),
        os.path.join(paths["app_data"], "Spotify"),
        os.path.join(paths["app_data"], "Zoom"),
        os.path.join(paths["app_data"], "Telegram Desktop"),
    ]

    for loc in known_user_locations:
        loc_norm = os.path.normpath(loc)
        loc_lower = loc_norm.lower()
        if os.path.isdir(loc_norm) and not is_reparse_point(loc_norm):
            if loc_lower not in tracked_source_dirs:
                item_name = os.path.basename(loc_norm)
                main_exe = find_main_executable(loc_norm, item_name)
                # Only include if an actual executable exists in the directory
                if not main_exe:
                    continue

                size_bytes = calculate_folder_size(loc_norm)
                if size_bytes == 0:
                    continue

                tracked_source_dirs.add(loc_lower)
                is_64 = is_binary_64bit(main_exe) if main_exe else True
                target_base = prog_files if is_64 else prog_files_x86
                target_path = os.path.join(target_base, item_name)

                running_pids = [
                    pid for pid, exe_p in running_procs.items()
                    if os.path.normpath(exe_p).lower().startswith(loc_lower)
                ]

                app_obj = InstalledApp(
                    name=item_name,
                    display_name=item_name,
                    source_path=loc_norm,
                    target_path=target_path,
                    size_bytes=size_bytes,
                    is_running=bool(running_pids),
                    running_pids=running_pids,
                    shortcuts=find_shortcuts_for_path(loc_norm, cached_shortcuts),
                    path_entries=_match_paths(loc_norm),
                    is_64bit=is_64,
                    main_exe=main_exe,
                )
                installed_apps.append(app_obj)

    return installed_apps
