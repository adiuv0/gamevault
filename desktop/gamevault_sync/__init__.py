"""GameVault Sync: keeps a gaming PC's screenshots backed up to a GameVault server."""

try:
    from ._version import __version__  # written by packaging/build.py for releases
except ImportError:
    __version__ = "1.1.0.dev0"

APP_NAME = "GameVault Sync"
