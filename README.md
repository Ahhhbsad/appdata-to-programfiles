# AppData to Program Files Migrator (WinMigrate)

Created by **[@Ahhhbsad](https://github.com/Ahhhbsad)**

A comprehensive Windows utility designed to migrate per-user installed applications from **AppData** (`%LocalAppData%\Programs`, `%AppData%`) to machine-wide **Program Files** (`C:\Program Files` or `C:\Program Files (x86)`), seamlessly updating the Windows Registry, environment variables, shortcuts, and file associations while preserving 100% backwards compatibility.

---

## 🚀 Key Features & Windows Tweaks

When an installer installs an app into a user's `AppData`, only that user can access it, and third-party tools, scripts, or system-wide services often cannot find it. This tool promotes those apps to global system status through the following tweaks:

1. **Automated Detection & Inspection**:
   - Discovers applications installed in `%LocalAppData%\Programs`, `%LocalAppData%`, `%AppData%`, and `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall`.
   - Inspects PE binary headers to automatically choose between `C:\Program Files` (64-bit) and `C:\Program Files (x86)` (32-bit).
   - Identifies active processes running from the application directory.

2. **File Migration with Robocopy & ACL Permissions**:
   - Uses native Windows `robocopy` with timestamp and attribute preservation (`/COPY:DAT /DCOPY:T /XJ`).
   - Configures NTFS Access Control Lists (`icacls`) granting `BUILTIN\Users:(OI)(CI)RX` permissions so all non-admin user accounts on the machine can execute the migrated app.

3. **NTFS Directory Junction Creation (`mklink /J`)**:
   - After moving files to `Program Files`, creates a transparent NTFS directory junction at the original `AppData` folder pointing to `C:\Program Files\<App>`.
   - **Why this matters**: Any hardcoded scripts, terminal commands (e.g. `code` CLI), extensions, or background tools looking for the old path continue to work without breaking!

4. **Deep Registry Migration (`HKCU` -> `HKLM`)**:
   - Copies the application's uninstall entry from `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\<Key>` to `HKLM\Software\Microsoft\Windows\CurrentVersion\Uninstall\<Key>`.
   - Deeply rewrites all string values (`REG_SZ`, `REG_EXPAND_SZ`), updating `InstallLocation`, `DisplayIcon`, `UninstallString`, `QuietUninstallString`, and `ModifyPath`.
   - Cleans up the `HKCU` uninstall key to avoid duplicate entries in Windows "Installed Apps" / Control Panel.

5. **Windows `App Paths` Registration**:
   - Registers the executable under `HKLM\Software\Microsoft\Windows\CurrentVersion\App Paths\<App.exe>` so users can launch the app directly from `Win + R` (Run prompt) or command line from anywhere.

6. **File Associations & Protocol Handlers (`Software\Classes`)**:
   - Detects custom URL schemes (e.g. `vscode://`, `obsidian://`) and file extensions registered under `HKCU\Software\Classes` and updates them with the new Program Files target.

7. **Shortcut (.lnk) Rewriting & All Users Promotion**:
   - Scans User Desktop and Start Menu for shortcuts pointing to the app.
   - Updates target path, working directory, and icon location.
   - Optionally copies shortcuts to **Public Desktop** (`C:\Users\Public\Desktop`) and **All Users Start Menu** (`C:\ProgramData\Microsoft\Windows\Start Menu\Programs`).

8. **Environment Variables (PATH) & OS Broadcast**:
   - Updates user and system `PATH` variables.
   - Broadcasts the Win32 `WM_SETTINGCHANGE` message (`HWND_BROADCAST`) so Windows Explorer and running processes recognize the PATH updates immediately without rebooting.

9. **Safety, Rollback & Dry-Run Mode**:
   - Exports the original registry key to a `.reg` file prior to migration.
   - Saves a detailed JSON manifest in `%ProgramData%\AppDataMigrator\Backups\`.
   - Features 1-click **Rollback** to revert files, registry, shortcuts, and PATH.
   - Includes **Dry Run / Preview Mode** to inspect all intended steps safely.

---

## 🖥️ User Interface

The tool provides both a **Modern Windows Desktop GUI** (dark theme, High-DPI aware, multithreaded) and a **Scriptable Command Line Interface (CLI)**.

### Running the Desktop GUI

Double-click `launch.bat` in the root folder, or run:

```cmd
python main.py
```

*Note: If not running as Administrator, the app displays a prominent status badge and allows 1-click UAC elevation.*

### Creating a Desktop Shortcut

Run `create_desktop_shortcut.bat` to create a dedicated shortcut on your Windows desktop.

---

## 💻 CLI Commands & Usage

For headless execution, automation, or scripts:

### 1. List user-installed applications:
```cmd
python cli.py list
```

### 2. Preview a migration (Dry Run):
```cmd
python cli.py migrate "Microsoft VS Code" --dry-run
```

### 3. Migrate an application:
```cmd
python cli.py migrate "Microsoft VS Code"
```

### 4. Migrate all detected user apps:
```cmd
python cli.py migrate --all
```

### 5. Options flags:
- `--dry-run`: Preview actions without touching files or registry.
- `--no-junction`: Skip creating the NTFS directory junction in AppData.
- `--no-all-users-shortcuts`: Keep shortcuts only in the current user profile.
- `--no-system-path`: Do not promote PATH to System environment.
- `--kill-running`: Automatically close active app processes before moving.

### 6. View past backups & rollback:
```cmd
python cli.py backups
python cli.py rollback [backup_id]
```

---

## 📁 Project Structure

```
appdata-to-programfiles/
│
├── core/
│   ├── models.py         # Data models (InstalledApp, MigrationOptions, Results)
│   ├── detector.py       # AppData & Registry scanner, 32/64-bit detection, size
│   ├── elevation.py      # Win32 UAC elevation and Administrator status checks
│   ├── junction.py       # NTFS directory junction creation and removal (mklink /J)
│   ├── registry.py       # Registry tree replication, string rewriting, App Paths
│   ├── shortcuts.py      # .lnk shortcut discovery, path rewriting, common locations
│   ├── env.py            # PATH variable updates and WM_SETTINGCHANGE broadcast
│   ├── migrator.py       # Migration orchestrator with Robocopy, ACLs, manifests
│   └── rollback.py       # Rollback manager and state restoration engine
│
├── gui/
│   └── app.py            # Modern High-DPI Tkinter GUI with dark styling & logs
│
├── tests/
│   └── test_migrator.py  # Unit test suite verifying scanner, registry, dry run
│
├── main.py               # Main launcher (GUI or CLI delegation)
├── cli.py                # Command-line interface
├── launch.bat            # Auto-elevating Windows batch launcher
├── create_desktop_shortcut.bat # Helper to generate desktop shortcut
└── README.md             # Complete documentation
```
