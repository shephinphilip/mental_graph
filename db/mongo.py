"""One Motor client per process. Routes must not construct their own."""

from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorClient

from config import get_settings


def create_mongo_client() -> AsyncIOMotorClient:
    settings = get_settings()
    uri = settings.MONGO_URI or settings.MONGODB_URI
    return AsyncIOMotorClient(
        uri,
        maxPoolSize=settings.MONGO_MAX_POOL_SIZE,
        minPoolSize=settings.MONGO_MIN_POOL_SIZE,
        serverSelectionTimeoutMS=settings.MONGO_SERVER_SELECTION_TIMEOUT_MS,
        connectTimeoutMS=settings.MONGO_CONNECT_TIMEOUT_MS,
        socketTimeoutMS=settings.MONGO_SOCKET_TIMEOUT_MS,
        retryWrites=True,
    )


def database_name() -> str:
    settings = get_settings()
    return settings.MONGO_DB_NAME or settings.DATABASE_NAME
