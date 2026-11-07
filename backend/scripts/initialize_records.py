"""Preview the records index. --apply explicitly permits creating this index."""
import argparse

from app.core.config import get_settings
from app.db import DatabaseConnection
from app.repositories.records import RECORD_INDEX, RECORD_INDEX_NAME, RecordsRepository


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    settings = get_settings()
    if args.database != settings.mongodb_database:
        parser.error("--database must match MONGODB_DATABASE")
    print("Database:", args.database)
    print("Collection: records; index:", RECORD_INDEX_NAME, RECORD_INDEX)
    if not args.apply:
        print("Preview only. No database request made.")
        return 0
    connection = DatabaseConnection(settings)
    try:
        connection.open()
        if connection.database is None:
            print("Database is unavailable; check local configuration.")
            return 1
        RecordsRepository(connection.database["records"]).ensure_indexes()
        print("Index is ready. No record documents were changed.")
        return 0
    except Exception as exc:
        print("Index setup failed (" + type(exc).__name__ + ").")
        return 1
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
