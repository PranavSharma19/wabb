from __future__ import annotations

import logging

from config import Settings, load_settings
from models.candidate import Candidate
from models.criteria import RecipientCriteria
from ranking.ranker import rank_candidates
from search.base import UserSearchClient
from search.mock_x_client import MockXClient
from search.query_builder import build_search_query, normalized_query_key
from search.x_client import XClient
from storage.cache import JsonSearchCache
from mock_x_platform.client import MockXPlatformClient


logger = logging.getLogger(__name__)


def find_users(
    criteria: RecipientCriteria,
    *,
    client: UserSearchClient | None = None,
    cache: JsonSearchCache | None = None,
    settings: Settings | None = None,
) -> list[Candidate]:
    """Search and rank at most ten candidates for future UI consumers."""

    active_settings = settings or load_settings()
    active_client = client or _make_client(active_settings)
    active_cache = cache or JsonSearchCache(active_settings.cache_path)
    query = build_search_query(criteria)
    key = f"{active_client.cache_namespace}:{normalized_query_key(query)}"

    cached = active_cache.get(key)
    if cached is not None:
        logger.info("[CACHE] Reusing previous search.")
        candidates = [Candidate.from_dict(item) for item in cached]
    else:
        label = (
            "MOCK X"
            if active_client.cache_namespace.casefold().startswith("mock")
            else "X API"
        )
        logger.info("[%s] Performing new user search...", label)
        candidates = active_client.search_users(query, max_results=10)[:10]
        active_cache.set(key, [candidate.to_dict() for candidate in candidates])

    return rank_candidates(candidates[:10], criteria)


def _make_client(settings: Settings) -> UserSearchClient:
    if settings.mock_x:
        if settings.mock_x_base_url:
            return MockXPlatformClient(
                settings.mock_x_base_url,
                timeout_seconds=settings.request_timeout_seconds,
            )
        return MockXClient()
    return XClient(settings.x_access_token, settings.request_timeout_seconds)
