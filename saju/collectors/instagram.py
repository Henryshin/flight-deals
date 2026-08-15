"""Instagram 해시태그 검색 수집기 (2단계 호출).

  1) GET /ig_hashtag_search?user_id=&q=사주   -> 해시태그 ID
  2) GET /{hashtag-id}/recent_media?user_id=&fields=...

권한: 비즈니스/크리에이터 계정 + instagram_basic + instagram_manage_insights.
한도: 7일 롤링 '고유 해시태그 30개'. 호출 수가 아니라 태그 종류가 한도라는 점이 핵심.
제약: recent_media 는 최근 24시간만 돌려준다. 따라서 추적할 태그는 '매일' 돌아야
      누락이 없고, 다행히 같은 태그 재조회는 쿼터를 더 쓰지 않는다.

해시태그 ID 는 불변이라 로컬에 캐시해 1단계 호출을 아낀다.
지식 코퍼스 관점에서 이 경로의 가치는 낮다 — 작성자 정보를 주지 않고 창도 24시간이라,
Threads 쪽을 주력으로 두는 편이 낫다. (saju/README.md 의 '한계' 절 참고)
"""
import json
import os
from pathlib import Path

from ._http import get_json
from .base import (
    MODE_HASHTAG, STATUS_AUTH, STATUS_NO_RESULTS, STATUS_OK, STATUS_PARSE,
    CollectResult, Collector, RawPost,
)

GRAPH_VERSION = os.environ.get("SAJU_IG_GRAPH_VERSION", "v21.0")
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"
FIELDS = "id,caption,permalink,timestamp,media_type"
TAG_ID_CACHE = Path(__file__).parent.parent / "data" / "hashtag_ids.json"


class InstagramCollector(Collector):
    name = "instagram"
    platform = "instagram"
    modes = (MODE_HASHTAG,)

    def __init__(self, _transport=None, token=None, user_id=None, cache_path=None):
        super().__init__(_transport=_transport)
        self.token = token or os.environ.get("IG_ACCESS_TOKEN", "")
        self.user_id = user_id or os.environ.get("IG_USER_ID", "")
        self.cache_path = Path(cache_path) if cache_path else TAG_ID_CACHE

    def available(self):
        missing = [
            name for name, val in
            (("IG_ACCESS_TOKEN", self.token), ("IG_USER_ID", self.user_id))
            if not val
        ]
        if missing:
            return False, f"{', '.join(missing)} 미설정"
        return True, ""

    def quota_key(self, query, mode):
        # 한도가 '고유 해시태그 수'이므로 태그 자체가 곧 쿼터 키다.
        return f"hashtag:{query.lstrip('#')}"

    # ---- 해시태그 ID 캐시 ----
    def _load_cache(self):
        if not self.cache_path.exists():
            return {}
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_cache(self, cache):
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _resolve_tag_id(self, tag, transport):
        """태그 -> ID. 캐시에 있으면 호출하지 않는다. (tag_id, status, detail)"""
        cache = self._load_cache()
        if tag in cache:
            return cache[tag], STATUS_OK, ""

        status, payload, detail = transport(
            f"{GRAPH_BASE}/ig_hashtag_search",
            {"user_id": self.user_id, "q": tag, "access_token": self.token},
        )
        if status != STATUS_OK:
            return None, status, detail
        data = (payload or {}).get("data") or []
        if not data or not data[0].get("id"):
            return None, STATUS_NO_RESULTS, f"해시태그를 찾지 못함: #{tag}"

        tag_id = str(data[0]["id"])
        cache[tag] = tag_id
        self._save_cache(cache)
        return tag_id, STATUS_OK, ""

    def search(self, query, mode=MODE_HASHTAG, limit=25):
        tag = query.lstrip("#")
        qkey = self.quota_key(tag, mode)
        ok, why = self.available()
        if not ok:
            return CollectResult(STATUS_AUTH, detail=why, quota_key=qkey)

        transport = self._transport or get_json
        tag_id, status, detail = self._resolve_tag_id(tag, transport)
        if tag_id is None:
            return CollectResult(status, detail=detail, quota_key=qkey)

        status, payload, detail = transport(
            f"{GRAPH_BASE}/{tag_id}/recent_media",
            {
                "user_id": self.user_id,
                "fields": FIELDS,
                "limit": limit,
                "access_token": self.token,
            },
        )
        if status != STATUS_OK:
            return CollectResult(status, detail=detail, quota_key=qkey)

        data = (payload or {}).get("data")
        if data is None:
            return CollectResult(
                STATUS_PARSE, quota_key=qkey,
                detail="응답에 data 필드가 없음 (API 구조 변경 의심)",
            )

        posts = [self._to_post(item, tag, mode) for item in data]
        return CollectResult(
            STATUS_OK if posts else STATUS_NO_RESULTS, posts=posts, quota_key=qkey,
        )

    def _to_post(self, item, tag, mode):
        # author 는 이 엔드포인트가 구조적으로 주지 않는다. 비어 있는 이유를 남겨두어야
        # 나중에 '익명 글'인지 '수집기 고장'인지 구분할 수 있다.
        missing = ["author"]
        if not item.get("timestamp"):
            missing.append("timestamp")
        return RawPost(
            platform=self.platform,
            post_id=str(item.get("id") or ""),
            text=item.get("caption") or "",
            permalink=item.get("permalink") or "",
            author=None,
            posted_at=item.get("timestamp"),
            media_type=item.get("media_type") or "",
            query=tag,
            query_mode=mode,
            api_fields_missing=missing,
        )
