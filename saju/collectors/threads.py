"""Threads 공식 keyword_search 수집기.

권한: threads_keyword_search (Meta 앱 심사 필요). 미승인 상태에서는 '본인 게시물'만
검색되므로, 심사 전에는 STATUS_PERMISSION 또는 빈 결과가 정상이다.
한도: 7일 롤링 500쿼리 — 쿼터 계산은 scripts/quota.py 가 담당한다.

토큰 없이도 필드 매핑과 오류 분류를 테스트할 수 있도록 _transport 를 주입받는다.
"""
import os

from ._http import get_json
from .base import (
    MODE_KEYWORD, MODE_TAG, STATUS_AUTH, STATUS_NO_RESULTS, STATUS_OK, STATUS_PARSE,
    CollectResult, Collector, RawPost,
)

GRAPH_VERSION = os.environ.get("SAJU_GRAPH_VERSION", "v1.0")
ENDPOINT = f"https://graph.threads.net/{GRAPH_VERSION}/keyword_search"
FIELDS = "id,text,permalink,timestamp,username,media_type,is_quote_post"


class ThreadsCollector(Collector):
    name = "threads"
    platform = "threads"
    modes = (MODE_KEYWORD, MODE_TAG)

    def __init__(self, _transport=None, token=None):
        super().__init__(_transport=_transport)
        self.token = token or os.environ.get("THREADS_ACCESS_TOKEN", "")

    def available(self):
        if not self.token:
            return False, "THREADS_ACCESS_TOKEN 미설정"
        return True, ""

    def search(self, query, mode=MODE_KEYWORD, limit=25):
        qkey = self.quota_key(query, mode)
        ok, why = self.available()
        if not ok:
            return CollectResult(STATUS_AUTH, detail=why, quota_key=qkey)

        params = {
            "q": query,
            "fields": FIELDS,
            "limit": limit,
            "access_token": self.token,
        }
        if mode == MODE_TAG:
            params["search_mode"] = "TAG"

        transport = self._transport or get_json
        status, payload, detail = transport(ENDPOINT, params)
        if status != STATUS_OK:
            return CollectResult(status, detail=detail, quota_key=qkey)

        data = (payload or {}).get("data")
        if data is None:
            return CollectResult(
                STATUS_PARSE, quota_key=qkey,
                detail="응답에 data 필드가 없음 (API 구조 변경 의심)",
            )

        posts = [self._to_post(item, query, mode) for item in data]
        return CollectResult(
            STATUS_OK if posts else STATUS_NO_RESULTS, posts=posts, quota_key=qkey,
        )

    def _to_post(self, item, query, mode):
        missing = [f for f in ("username", "timestamp") if not item.get(f)]
        return RawPost(
            platform=self.platform,
            post_id=str(item.get("id") or ""),
            text=item.get("text") or "",
            permalink=item.get("permalink") or "",
            author=item.get("username"),
            posted_at=item.get("timestamp"),
            media_type=item.get("media_type") or "TEXT_POST",
            query=query,
            query_mode=mode,
            api_fields_missing=missing,
        )
