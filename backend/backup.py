"""Command-line entry point for GameVault state backups."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from backend.services.backup_service import (
    BackupError,
    create_state_backup,
    verify_state_backup,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.backup",
        description="Create or verify a live GameVault database-and-key backup.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="Create a verified online state backup")
    create.add_argument("destination", type=Path, help="Directory that will hold bundles")

    verify = commands.add_parser("verify", help="Verify an existing state backup")
    verify.add_argument("bundle", type=Path, help="Path to a state backup bundle")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            bundle = create_state_backup(args.destination)
            verification = verify_state_backup(bundle)
        else:
            verification = verify_state_backup(args.bundle)
    except BackupError as error:
        parser.exit(1, f"error: {error}\n")

    print(
        json.dumps(
            {
                "status": "ok",
                "bundle": str(verification.bundle_path),
                "database_sha256": verification.database_sha256,
                "database_size_bytes": verification.database_size_bytes,
                "migration_count": len(verification.applied_migrations),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
