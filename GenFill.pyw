"""Double-click / shortcut entry point (no console window)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from genfill.app import main  # noqa: E402

main()
