"""Run PRIMUS from its project root."""
from pathlib import Path
import sys
root = Path(__file__).resolve().parent
vendor = root / "vendor"
if vendor.is_dir():
    sys.path.insert(0, str(vendor))
sys.path.insert(0, str(root / "src"))
from primus.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
