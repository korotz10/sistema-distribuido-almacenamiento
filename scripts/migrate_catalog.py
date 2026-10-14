"""Exportar, una sola vez, el catálogo SQLite de la versión anterior a JSON."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coordinator.catalog import CatalogManager


def migrate(source, destination):
    source, destination = Path(source), Path(destination)
    if not source.is_file():
        raise ValueError("No existe el catálogo SQLite de origen")
    # Solo lectura: no se modifican el catálogo anterior ni los objetos de los nodos.
    connection = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        records = {row["filename"]: {**dict(row), "status": "COMMITTED"}
                   for row in connection.execute("SELECT * FROM files")}
        index = connection.execute("SELECT value FROM metadata WHERE key='round_robin_index'").fetchone()
        data = CatalogManager.validate({"round_robin_index": int(index[0]) if index else 0, "files": records})
    finally:
        connection.close()
    # 'x' rechaza un destino existente; nunca sobrescribir una instalación.
    with destination.open("x", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    return len(records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        count = migrate(args.source, args.destination)
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, f"No se pudo exportar: {exc}\n")
    print(f"Exportados {count} archivos a {args.destination}. SQLite se conserva.")


if __name__ == "__main__":
    main()
