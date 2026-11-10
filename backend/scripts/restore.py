"""Restore an A.R.C.H. data backup archive."""

import argparse
from pathlib import Path

from arch.backup import restore_backup
from arch.settings import get_settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    restore_backup(args.archive, get_settings().data_dir, replace=args.replace)


if __name__ == "__main__":
    main()
