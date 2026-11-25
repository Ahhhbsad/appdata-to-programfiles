"""
NTFS Directory Junction and Reparse Point Management.
Creates and manages junctions from AppData to Program Files.
"""
import os
import sys
import subprocess
import ctypes
from typing import Tuple, Optional
from pathlib import Path


FILE_ATTRIBUTE_REPARSE_POINT = 0x0400


def is_reparse_point(path: str) -> bool:
    """Return True if the path is an NTFS reparse point (junction or symlink)."""
    if not os.path.exists(path):
        return False
    try:
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        if attrs == -1:
            return False
        return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)
    except Exception:
        return False


def get_junction_target(path: str) -> Optional[str]:
    """Retrieve the target directory of an NTFS junction point."""
    if not is_reparse_point(path):
        return None
    try:
        # Use PowerShell or fsutil to inspect reparse point
        ps_cmd = f"(Get-Item -LiteralPath '{path}').Target"
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=10
        )
        target = res.stdout.strip()
        if target and os.path.exists(target):
            return target
    except Exception:
        pass
    return None


def create_junction(link_path: str, target_path: str) -> Tuple[bool, str]:
    """
    Create an NTFS directory junction at link_path pointing to target_path.
    mklink /J creates an NTFS junction (does not require special SeCreateSymbolicLinkPrivilege).
    """
    link_path = os.path.abspath(link_path)
    target_path = os.path.abspath(target_path)

    if not os.path.exists(target_path):
        return False, f"Target directory does not exist: {target_path}"

    if os.path.exists(link_path):
        if is_reparse_point(link_path):
            existing_target = get_junction_target(link_path)
            if existing_target and os.path.samefile(existing_target, target_path):
                return True, "Junction already exists and points to target."
            return False, f"A junction already exists at {link_path} pointing to {existing_target}"
        return False, f"Cannot create junction: a regular file or directory already exists at {link_path}"

    # Ensure parent directory of link_path exists
    os.makedirs(os.path.dirname(link_path), exist_ok=True)

    cmd = f'cmd.exe /c mklink /J "{link_path}" "{target_path}"'
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=15
        )
        if proc.returncode == 0 and os.path.exists(link_path):
            return True, f"Junction successfully created: {link_path} -> {target_path}"
        else:
            err = proc.stderr.strip() or proc.stdout.strip()
            return False, f"Failed to create junction: {err}"
    except Exception as e:
        return False, f"Exception while creating junction: {e}"


def remove_junction(link_path: str) -> Tuple[bool, str]:
    """
    Safely removes an NTFS directory junction without deleting target files.
    Using rmdir / os.rmdir on a junction only unlinks the junction.
    """
    link_path = os.path.abspath(link_path)
    if not os.path.exists(link_path):
        return True, "Path does not exist."

    if not is_reparse_point(link_path):
        return False, f"Refusing to remove: {link_path} is NOT a junction/reparse point."

    try:
        # os.rmdir safely removes the junction on Windows without touching target contents
        os.rmdir(link_path)
        return True, f"Junction removed: {link_path}"
    except Exception as e:
        # Fallback to cmd rmdir
        try:
            cmd = f'cmd.exe /c rmdir "{link_path}"'
            proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
            if proc.returncode == 0:
                return True, f"Junction removed via rmdir: {link_path}"
            return False, f"Failed to remove junction: {proc.stderr.strip()}"
        except Exception as e2:
            return False, f"Error removing junction: {e2}"
