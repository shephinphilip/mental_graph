"""Cached school bundle. The cache key always includes the tenant."""

from __future__ import annotations

from dashboard.cache import cache
from dashboard.constants import CACHE_TTL_SECONDS
from dashboard.metrics import Scope
from dashboard.repositories.dashboard_repository import SchoolBundle, load_bundle
from dashboard.services.compute import Prepared, prepare


async def get_bundle(db, actor, scope: Scope) -> SchoolBundle:
    key = f"dashboard:bundle|{actor.school_key}|{scope.cache_token()}"
    cached = cache.get(key)
    if isinstance(cached, SchoolBundle):
        return cached
    bundle = await load_bundle(db, actor.query_user, actor.school_key)
    cache.set(key, bundle, CACHE_TTL_SECONDS)
    return bundle


async def get_prepared(db, actor, scope: Scope) -> Prepared:
    bundle = await get_bundle(db, actor, scope)
    return prepare(bundle, scope)
