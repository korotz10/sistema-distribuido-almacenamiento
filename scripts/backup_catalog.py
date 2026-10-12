"""Respaldo consistente mediante la API de backup de SQLite."""
import argparse
from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from common.config import CATALOG_DB
from coordinator.catalog import CatalogManager


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--source", type=Path, default=CATALOG_DB)
    args = parser.parse_args()
    if not args.source.is_file():
        parser.error("El catálogo origen no existe")
    if args.destination.exists():
        parser.error("Elige una ruta de respaldo nueva; no se sobrescriben respaldos")
    CatalogManager(args.source).backup(args.destination)
    print(f"Respaldo guardado en {args.destination.resolve()}")


if __name__ == "__main__":
    main()
