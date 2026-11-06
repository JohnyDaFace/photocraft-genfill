"""Command-line version of the app's "Check compatibility" button (see genfill/compat.py).

    .venv\\Scripts\\python.exe tools\\compat_check.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from genfill.compat import run_checks  # noqa: E402

if __name__ == "__main__":
    sys.exit(0 if run_checks() else 1)
