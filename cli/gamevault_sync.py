"""Starts GameVault Sync, which now lives in desktop/ as a proper app.

Kept so existing shortcuts and scripts keep working; every option of the old
script is still accepted (``--no-gui --server --token --mode --steam-path
--specialk-path --dry-run``). Most people want the Windows installer instead;
see desktop/README.md.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "desktop"))

try:
    from gamevault_sync.__main__ import main
except ImportError as exc:  # pragma: no cover - depends on the local Python
    sys.exit(
        f"GameVault Sync needs a few packages ({exc.name}). Install them with:\n"
        "    pip install -r desktop/requirements.txt\n"
        "or download the Windows app: https://github.com/adiuv0/gamevault/releases/latest"
    )

if __name__ == "__main__":
    sys.exit(main())
