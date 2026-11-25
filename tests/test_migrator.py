"""
Verification tests for AppData to Program Files Migrator.
"""
import sys
import os
import unittest

# Ensure project root in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.models import InstalledApp, MigrationOptions, MigrationResult
from core.detector import scan_for_installed_apps, is_binary_64bit
from core.registry import _replace_in_value
from core.junction import is_reparse_point
from core.migrator import Migrator
from core.rollback import RollbackManager


class TestAppDataMigrator(unittest.TestCase):

    def test_registry_replacement(self):
        old_dir = r"C:\Users\John\AppData\Local\Programs\MyApp"
        new_dir = r"C:\Program Files\MyApp"

        # Test simple string
        raw_val = r"C:\Users\John\AppData\Local\Programs\MyApp\app.exe"
        replaced = _replace_in_value(raw_val, 1, old_dir, new_dir)
        self.assertEqual(replaced, r"C:\Program Files\MyApp\app.exe")

        # Test case insensitivity
        raw_lower = r"c:\users\john\appdata\local\programs\myapp\bin\tool.exe"
        replaced_lower = _replace_in_value(raw_lower, 1, old_dir, new_dir)
        self.assertEqual(replaced_lower, r"C:\Program Files\MyApp\bin\tool.exe")

        # Test multi-sz list
        raw_list = [
            r"C:\Users\John\AppData\Local\Programs\MyApp",
            r"C:\Windows\System32"
        ]
        replaced_list = _replace_in_value(raw_list, 7, old_dir, new_dir)
        self.assertEqual(replaced_list[0], r"C:\Program Files\MyApp")
        self.assertEqual(replaced_list[1], r"C:\Windows\System32")

    def test_scanner_finds_apps(self):
        apps = scan_for_installed_apps()
        self.assertIsInstance(apps, list)
        self.assertGreater(len(apps), 0, "Should detect installed user apps in AppData")

        # Check known apps
        names = [a.name for a in apps]
        print(f"\n[Test] Detected apps: {names}")
        self.assertTrue(any("VS Code" in n or "Obsidian" in n or "ZapFast" in n for n in names))

    def test_dry_run_simulation(self):
        apps = scan_for_installed_apps()
        self.assertGreater(len(apps), 0)
        target_app = apps[0]

        logs = []
        migrator = Migrator(log_callback=lambda m: logs.append(m))
        options = MigrationOptions(dry_run=True)

        res = migrator.migrate(target_app, options)
        self.assertTrue(res.success)
        self.assertGreaterEqual(len(res.steps), 3)
        self.assertTrue(any("DryRun" in s.step for s in res.steps))
        print(f"\n[Test] Dry run successfully executed for {target_app.name}")

    def test_rollback_manager_initialization(self):
        mgr = RollbackManager()
        backups = mgr.list_available_backups()
        self.assertIsInstance(backups, list)


if __name__ == "__main__":
    unittest.main()
