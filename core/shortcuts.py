"""
Windows Shortcut (.lnk) discovery, path rewriting, and migration.
"""
import os
import sys
import shutil
import subprocess
import json
import base64
from typing import List, Dict, Any, Tuple, Optional
from .models import ShortcutInfo


def get_shortcut_directories() -> Dict[str, str]:
    """Retrieve standard user and all-users shortcut directories."""
    user_profile = os.environ.get("USERPROFILE", "")
    app_data = os.environ.get("APPDATA", "")
    program_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
    public_dir = os.environ.get("PUBLIC", r"C:\Users\Public")

    return {
        "user_desktop": os.path.join(user_profile, "Desktop"),
        "user_start_menu": os.path.join(app_data, r"Microsoft\Windows\Start Menu\Programs"),
        "public_desktop": os.path.join(public_dir, "Desktop"),
        "common_start_menu": os.path.join(program_data, r"Microsoft\Windows\Start Menu\Programs"),
    }


def _run_ps(script: str) -> str:
    """Run a PowerShell script reliably."""
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True,
        text=True,
        timeout=30
    )
    return proc.stdout.strip()


def get_all_user_shortcuts() -> List[ShortcutInfo]:
    """
    Scan User Desktop and User Start Menu once and return all .lnk shortcuts.
    """
    dirs = get_shortcut_directories()
    search_roots = [
        (dirs["user_desktop"], True, False),
        (dirs["user_start_menu"], False, True),
    ]

    lnk_files = []
    for root_dir, is_desktop, is_start_menu in search_roots:
        if not os.path.exists(root_dir):
            continue
        for root, _, files in os.walk(root_dir):
            for file in files:
                if file.lower().endswith(".lnk"):
                    full_path = os.path.join(root, file)
                    lnk_files.append((full_path, is_desktop, is_start_menu))

    if not lnk_files:
        return []

    paths_list = [f'"{f[0]}"' for f in lnk_files]
    ps_script = f"""
$sh = New-Object -ComObject WScript.Shell
$results = @()
$paths = @({', '.join(paths_list)})
foreach ($p in $paths) {{
    try {{
        if (Test-Path -LiteralPath $p) {{
            $sc = $sh.CreateShortcut($p)
            $results += [PSCustomObject]@{{
                Path = $p
                Target = $sc.TargetPath
                Args = $sc.Arguments
                WorkDir = $sc.WorkingDirectory
                Icon = $sc.IconLocation
            }}
        }}
    }} catch {{}}
}}
$results | ConvertTo-Json -Compress
"""
    found_shortcuts: List[ShortcutInfo] = []
    try:
        raw_json = _run_ps(ps_script)
        if not raw_json:
            return []

        data = json.loads(raw_json)
        if isinstance(data, dict):
            data = [data]

        path_flags = {item[0]: (item[1], item[2]) for item in lnk_files}
        for entry in data:
            target = entry.get("Target", "")
            if not target:
                continue
            lnk_path = entry.get("Path", "")
            is_desk, is_start = path_flags.get(lnk_path, (False, False))
            found_shortcuts.append(ShortcutInfo(
                lnk_path=lnk_path,
                target_path=target,
                arguments=entry.get("Args", ""),
                working_dir=entry.get("WorkDir", ""),
                icon_location=entry.get("Icon", ""),
                is_user_desktop=is_desk,
                is_user_start_menu=is_start,
            ))
    except Exception as e:
        print(f"Error querying shortcuts: {e}", file=sys.stderr)

    return found_shortcuts


def find_shortcuts_for_path(source_dir: str, cached_shortcuts: Optional[List[ShortcutInfo]] = None) -> List[ShortcutInfo]:
    """
    Filter shortcuts (.lnk) pointing to source_dir.
    """
    source_dir_norm = os.path.normpath(source_dir).lower()
    all_shortcuts = cached_shortcuts if cached_shortcuts is not None else get_all_user_shortcuts()
    return [
        sc for sc in all_shortcuts
        if os.path.normpath(sc.target_path).lower().startswith(source_dir_norm + os.sep)
        or os.path.normpath(sc.target_path).lower() == source_dir_norm
    ]


def update_shortcut_paths(
    shortcut_path: str,
    old_dir: str,
    new_dir: str
) -> Tuple[bool, str]:
    """
    Update a .lnk file, replacing occurrences of old_dir with new_dir in Target, WorkDir, and Icon.
    """
    ps_script = f"""
$sh = New-Object -ComObject WScript.Shell
$p = '{shortcut_path}'
$oldDir = '{old_dir}'
$newDir = '{new_dir}'

try {{
    $sc = $sh.CreateShortcut($p)
    if ($sc.TargetPath -like "*$oldDir*") {{
        $sc.TargetPath = $sc.TargetPath.Replace($oldDir, $newDir)
    }}
    if ($sc.WorkingDirectory -like "*$oldDir*") {{
        $sc.WorkingDirectory = $sc.WorkingDirectory.Replace($oldDir, $newDir)
    }}
    if ($sc.IconLocation -like "*$oldDir*") {{
        $sc.IconLocation = $sc.IconLocation.Replace($oldDir, $newDir)
    }}
    $sc.Save()
    Write-Output "SUCCESS"
}} catch {{
    Write-Output $_.Exception.Message
}}
"""
    try:
        out = _run_ps(ps_script)
        if "SUCCESS" in out:
            return True, f"Updated shortcut: {shortcut_path}"
        return False, f"Failed updating shortcut: {out}"
    except Exception as e:
        return False, str(e)


def migrate_shortcut_to_common(
    shortcut_path: str,
    is_desktop: bool,
    is_start_menu: bool
) -> Tuple[bool, str]:
    """
    Copies or creates an equivalent shortcut in Public Desktop or Common Start Menu.
    """
    dirs = get_shortcut_directories()
    file_name = os.path.basename(shortcut_path)

    try:
        dest_path = None
        if is_desktop:
            dest_path = os.path.join(dirs["public_desktop"], file_name)
        elif is_start_menu:
            # Replicate relative subfolder structure if inside a subfolder
            rel = os.path.relpath(os.path.dirname(shortcut_path), dirs["user_start_menu"])
            if rel and rel != ".":
                target_folder = os.path.join(dirs["common_start_menu"], rel)
            else:
                target_folder = dirs["common_start_menu"]
            os.makedirs(target_folder, exist_ok=True)
            dest_path = os.path.join(target_folder, file_name)

        if dest_path:
            shutil.copy2(shortcut_path, dest_path)
            return True, f"Copied shortcut to common location: {dest_path}"
        return False, "Neither desktop nor start menu location identified."
    except Exception as e:
        return False, f"Failed copying to common location: {e}"
