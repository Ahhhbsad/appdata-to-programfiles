"""
Main Application Entry Point.
Launches the Modern GUI or delegates to CLI based on command line arguments.
"""
import sys
import os

# Ensure current directory is on sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))


def main():
    cli_commands = {"list", "scan", "migrate", "rollback", "backups", "--help", "-h"}

    # If explicit CLI argument or command given
    if len(sys.argv) > 1 and any(arg in cli_commands for arg in sys.argv[1:]):
        from cli import main as cli_main
        cli_main()
        return

    # Check for --cli flag
    if "--cli" in sys.argv:
        sys.argv.remove("--cli")
        from cli import main as cli_main
        cli_main()
        return

    # Default to GUI
    try:
        from gui.app import run_gui
        run_gui()
    except Exception as e:
        # Fallback to CLI if GUI cannot initialize (e.g. headless or DISPLAY missing)
        print(f"Notice: GUI initialization failed ({e}). Falling back to CLI mode.\n")
        from cli import main as cli_main
        cli_main()


if __name__ == "__main__":
    main()
