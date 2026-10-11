"""Entry point PyInstaller bundles; the app lives in the gamevault_sync package."""

import sys

from gamevault_sync.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
