"""One synchronous PyMongo pool per FastAPI application lifespan."""
import logging
from typing import Any

from fastapi import HTTPException, Request

from app.core.config import Settings

logger = logging.getLogger(__name__)


def create_mongo_client(uri: str) -> Any:
    # Delayed import keeps health/auth independent of MongoDB installation/config.
    from pymongo import MongoClient

    return MongoClient(
        uri,
        connect=False,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        socketTimeoutMS=5000,
        tz_aware=True,
        appname="raphael",
    )


class DatabaseConnection:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client: Any = None
        self.database: Any = None

    def open(self) -> None:
        if self.client is not None:
            return
        if not self.settings.mongodb_uri or not self.settings.mongodb_database:
            return
        try:
            self.client = create_mongo_client(self.settings.mongodb_uri)
            self.database = self.client[self.settings.mongodb_database]
        except Exception as exc:
            # Driver exceptions can contain URIs and credentials; log only the type.
            logger.warning("MongoDB initialization failed (%s)", type(exc).__name__)
            self.close()

    def close(self) -> None:
        client, self.client, self.database = self.client, None, None
        if client is not None:
            try:
                client.close()
            except Exception as exc:
                logger.warning("MongoDB cleanup failed (%s)", type(exc).__name__)


def get_database(request: Request) -> Any:
    connection = getattr(request.app.state, "database_connection", None)
    if connection is None or connection.database is None:
        raise HTTPException(503, "資料庫尚未設定完成或暫時無法使用")
    return connection.database
