"""
Windows UAC Elevation Utilities.
Detects if the process has Administrator privileges and provides re-launch with elevation.
"""
import sys
import os
import ctypes
from typing import List, Optional


def is_admin() -> bool:
    """Return True if the current process runs with Administrator privileges."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def request_admin(args: Optional[List[str]] = None, wait: bool = False) -> bool:
    """
    Relaunches the current python script with Administrator privileges via UAC prompt.
    Returns True if the elevation request was dispatched, False otherwise.
    """
    if is_admin():
        return True

    if args is None:
        args = sys.argv[1:]

    # Prepare command line
    script_path = os.path.abspath(sys.argv[0])
    quoted_args = [f'"{script_path}"'] + [f'"{a}"' if ' ' in a else a for a in args]
    parameters = " ".join(quoted_args)

    try:
        # ShellExecuteExW or ShellExecuteW with "runas"
        hinstance = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            sys.executable,
            parameters,
            os.getcwd(),
            1  # SW_SHOWNORMAL
        )
        # ShellExecute returns > 32 on success
        if hinstance > 32:
            return True
        else:
            return False
    except Exception as e:
        print(f"Elevation request error: {e}", file=sys.stderr)
        return False
