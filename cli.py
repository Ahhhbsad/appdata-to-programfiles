"""
Command Line Interface for AppData to Program Files Migrator.
"""
import sys
import os
import argparse
from typing import List, Optional

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from core.models import InstalledApp, MigrationOptions
from core.detector import scan_for_installed_apps
from core.migrator import Migrator
from core.rollback import RollbackManager
from core.elevation import is_admin, request_admin


def print_banner():
    banner = r"""
=============================================================
  AppData -> Program Files Migrator (Windows System Utility)
=============================================================
"""
    print(banner)


def format_table(headers: List[str], rows: List[List[str]]) -> str:
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            if i < len(col_widths):
                col_widths[i] = max(col_widths[i], len(str(val)))

    sep = "+-" + "-+-".join("-" * w for w in col_widths) + "-+"
    hdr_line = "| " + " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers)) + " |"

    lines = [sep, hdr_line, sep]
    for row in rows:
        line = "| " + " | ".join(str(val).ljust(col_widths[i]) for i, val in enumerate(row)) + " |"
        lines.append(line)
    lines.append(sep)
    return "\n".join(lines)


def cmd_list(args):
    print_banner()
    print("Scanning system for user-installed applications in AppData...")
    apps = scan_for_installed_apps()

    if not apps:
        print("\nNo user-installed applications found in AppData/Programs.")
        return

    headers = ["#", "Application Name", "Size", "Status", "Source (AppData)", "Target (Program Files)"]
    rows = []
    for idx, app in enumerate(apps, 1):
        status = "RUNNING" if app.is_running else "Stopped"
        rows.append([
            str(idx),
            app.display_name or app.name,
            app.formatted_size,
            status,
            app.source_path,
            app.target_path
        ])

    print(f"\nFound {len(apps)} user-installed application(s):\n")
    print(format_table(headers, rows))
    print(f"\nAdmin Status: {'[OK] Running as Administrator' if is_admin() else '[!] Standard User (Admin required for migration)'}")


def cmd_migrate(args):
    print_banner()
    if not is_admin() and not args.dry_run:
        print("Administrator rights required. Attempting elevation prompt...")
        if request_admin():
            print("Elevation prompt launched. Exiting standard process.")
            return
        else:
            print("Error: Could not obtain Administrator privileges. Please run CMD/PowerShell as Administrator.")
            sys.exit(1)

    print("Scanning apps...")
    apps = scan_for_installed_apps()
    if not apps:
        print("No apps found.")
        return

    targets: List[InstalledApp] = []
    if args.all:
        targets = apps
    elif args.app:
        query = args.app.lower()
        for a in apps:
            if query in a.name.lower() or query in a.display_name.lower():
                targets.append(a)

        if not targets:
            print(f"No application found matching query: '{args.app}'")
            print("Available applications:")
            for a in apps:
                print(f" - {a.display_name or a.name}")
            return
    else:
        print("Error: Please specify an application name to migrate or use --all.")
        return

    options = MigrationOptions(
        create_junction=not args.no_junction,
        migrate_to_all_users_shortcuts=not args.no_all_users_shortcuts,
        update_system_path=not args.no_system_path,
        create_backup=True,
        kill_running_processes=args.kill_running,
        dry_run=args.dry_run
    )

    migrator = Migrator()
    for app in targets:
        result = migrator.migrate(app, options)
        if not result.success:
            print(f"\n[FAILED] Could not migrate {app.display_name}: {result.error_message}")
        else:
            print(f"\n[SUCCESS] Successfully migrated {app.display_name}!")


def cmd_backups(args):
    print_banner()
    mgr = RollbackManager()
    backups = mgr.list_available_backups()
    if not backups:
        print("No migration backups found in %ProgramData%\\AppDataMigrator\\Backups.")
        return

    headers = ["Backup ID", "Application", "Source (Original)", "Timestamp", "Rolled Back?"]
    rows = []
    for b in backups:
        is_rb = "YES" if b.get("rolled_back_at") else "No"
        rows.append([
            b.get("backup_id", ""),
            b.get("app_name", ""),
            b.get("source_path", ""),
            b.get("timestamp", ""),
            is_rb
        ])
    print(format_table(headers, rows))


def cmd_rollback(args):
    print_banner()
    if not is_admin():
        print("Administrator rights required for rollback. Attempting elevation prompt...")
        if request_admin():
            return
        else:
            print("Error: Could not obtain Administrator privileges.")
            sys.exit(1)

    mgr = RollbackManager()
    backups = mgr.list_available_backups()
    if not backups:
        print("No backups available to roll back.")
        return

    backup_id = args.id
    if not backup_id:
        print("Available Backups:")
        for idx, b in enumerate(backups, 1):
            rb = " (Already Rolled Back)" if b.get("rolled_back_at") else ""
            print(f" [{idx}] {b.get('backup_id')} - {b.get('app_name')}{rb}")

        choice = input("\nEnter number or backup ID to restore (or 'q' to cancel): ").strip()
        if choice.lower() == 'q':
            return
        if choice.isdigit() and 1 <= int(choice) <= len(backups):
            backup_id = backups[int(choice) - 1]["backup_id"]
        else:
            backup_id = choice

    ok, msg = mgr.rollback(backup_id)
    if ok:
        print(f"\n[SUCCESS] {msg}")
    else:
        print(f"\n[ERROR] {msg}")


def main():
    parser = argparse.ArgumentParser(
        description="Transfer user-installed applications from AppData to Program Files (Global/Machine-wide)."
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # list
    p_list = subparsers.add_parser("list", help="List detected user-installed apps in AppData")
    p_list.set_defaults(func=cmd_list)

    # scan alias
    p_scan = subparsers.add_parser("scan", help="Scan and list apps (alias of list)")
    p_scan.set_defaults(func=cmd_list)

    # migrate
    p_migrate = subparsers.add_parser("migrate", help="Migrate an application to Program Files")
    p_migrate.add_argument("app", nargs="?", help="Name or keyword of app to migrate")
    p_migrate.add_argument("--all", action="store_true", help="Migrate all discovered apps")
    p_migrate.add_argument("--dry-run", action="store_true", help="Preview actions without modifying anything")
    p_migrate.add_argument("--no-junction", action="store_true", help="Do not create NTFS directory junction")
    p_migrate.add_argument("--no-all-users-shortcuts", action="store_true", help="Keep shortcuts only in user profile")
    p_migrate.add_argument("--no-system-path", action="store_true", help="Do not promote PATH entries to System PATH")
    p_migrate.add_argument("--kill-running", action="store_true", help="Automatically terminate running app processes")
    p_migrate.set_defaults(func=cmd_migrate)

    # backups
    p_backups = subparsers.add_parser("backups", help="List past migration backups")
    p_backups.set_defaults(func=cmd_backups)

    # rollback
    p_rollback = subparsers.add_parser("rollback", help="Restore an application back to AppData")
    p_rollback.add_argument("id", nargs="?", help="Backup ID to rollback")
    p_rollback.set_defaults(func=cmd_rollback)

    args = parser.parse_args()
    if not args.command:
        # Default to listing if no args
        cmd_list(args)
    else:
        args.func(args)


if __name__ == "__main__":
    main()
