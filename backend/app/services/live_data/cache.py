"""
Ephemeral In-Memory Live Data Cache (DPDP Act 2023 Compliant).

Constraints:
- Cache live records strictly in-memory (RAM only).
- Never persist to SQLite, disk, files, or audit logs.
- Short TTL (default: 180 seconds, max 300 seconds).
- Keyed strictly by (roll_number, feature).
- Thread-safe eviction.
- Zero student personal data is logged.
"""

import logging
import threading
import time
from typing import Any, Dict, Optional, Tuple

from backend.app.core.config import settings

logger = logging.getLogger(__name__)


class EphemeralLiveCache:
    """Thread-safe, in-memory ephemeral cache for live Anvaya student records."""

    def __init__(self, default_ttl_seconds: int = 180):
        self._cache: Dict[Tuple[str, str], Tuple[Dict[str, Any], float]] = {}
        self._lock = threading.Lock()
        self._default_ttl = default_ttl_seconds

    def _clean_key(self, roll_number: str, feature: str) -> Tuple[str, str]:
        return (str(roll_number).strip().upper(), str(feature).strip().lower())

    def get(self, roll_number: str, feature: str) -> Optional[Dict[str, Any]]:
        """Retrieves cached live data if not expired. Returns None on miss or expiry."""
        key = self._clean_key(roll_number, feature)
        now = time.time()
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            data, expires_at = entry
            if now > expires_at:
                # Expired - evict
                del self._cache[key]
                return None
            return data

    def set(
        self,
        roll_number: str,
        feature: str,
        data: Dict[str, Any],
        ttl_seconds: Optional[int] = None,
    ) -> None:
        """Stores live data in memory with TTL. Never writes to disk."""
        key = self._clean_key(roll_number, feature)
        ttl = ttl_seconds if ttl_seconds is not None else getattr(settings, "LIVE_CACHE_TTL_SECONDS", self._default_ttl)
        ttl = min(ttl, 300)  # Maximum 5 minutes per DPDP Act compliance
        expires_at = time.time() + ttl
        with self._lock:
            self._cache[key] = (data, expires_at)

    def invalidate(self, roll_number: Optional[str] = None, feature: Optional[str] = None) -> int:
        """Invalidates entries for a student and/or feature."""
        count = 0
        clean_roll = str(roll_number).strip().upper() if roll_number else None
        clean_feat = str(feature).strip().lower() if feature else None
        with self._lock:
            keys_to_delete = [
                k for k in self._cache.keys()
                if (clean_roll is None or k[0] == clean_roll)
                and (clean_feat is None or k[1] == clean_feat)
            ]
            for k in keys_to_delete:
                del self._cache[k]
                count += 1
        return count

    def clear(self) -> None:
        """Clears all ephemeral in-memory entries."""
        with self._lock:
            self._cache.clear()


# Global in-memory cache singleton
ephemeral_live_cache = EphemeralLiveCache(default_ttl_seconds=180)
