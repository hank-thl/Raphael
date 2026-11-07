"""Read-only MongoDB ping: no collections, indexes, or documents are written."""
from app.core.config import get_settings
from app.db import DatabaseConnection


def main() -> int:
    connection = DatabaseConnection(get_settings())
    if not connection.settings.mongodb_uri or not connection.settings.mongodb_database:
        print("Set MONGODB_URI and MONGODB_DATABASE in backend/.env first.")
        return 1
    try:
        connection.open()
        if connection.client is None:
            print("MongoDB initialization failed; check local configuration and installed driver.")
            return 1
        connection.client.admin.command("ping")
        print("MongoDB ping passed. No documents or indexes were written.")
        print("Database permissions and CRUD are not verified by ping.")
        return 0
    except Exception as exc:
        print("MongoDB ping failed (" + type(exc).__name__ + "). Check Atlas access/configuration.")
        return 1
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
