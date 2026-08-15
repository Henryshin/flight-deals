"""픽스처 재생 수집기 — 자격증명 0으로 전 파이프라인을 돌리기 위한 것.

앱 심사가 끝나기 전까지 실제 수집을 할 수 없으므로, 정규화·태깅·품질·중복제거·
코퍼스 빌드 전 구간을 오늘 검증하려면 이게 필요하다. 실제 오케스트레이션 경로를
그대로 타므로 '샘플이라 다른 코드' 가 아니다.
"""
import json
from pathlib import Path

from .base import (
    MODE_HASHTAG, MODE_KEYWORD, MODE_TAG, STATUS_ERROR, STATUS_NO_RESULTS, STATUS_OK,
    CollectResult, Collector, RawPost,
)

FIXTURE_FILE = Path(__file__).parent.parent / "fixtures" / "sample_posts.json"


class SampleCollector(Collector):
    name = "sample"
    platform = "sample"
    modes = (MODE_KEYWORD, MODE_TAG, MODE_HASHTAG)

    def __init__(self, _transport=None, fixture_path=None):
        super().__init__(_transport=_transport)
        self.fixture_path = Path(fixture_path) if fixture_path else FIXTURE_FILE

    def available(self):
        if not self.fixture_path.exists():
            return False, f"픽스처 없음: {self.fixture_path}"
        return True, ""

    def search(self, query, mode=MODE_KEYWORD, limit=25):
        try:
            raw = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            return CollectResult(STATUS_ERROR, detail=f"픽스처 읽기 실패: {e}")

        items = raw.get("posts", raw if isinstance(raw, list) else [])
        # 검색어가 본문이나 태그에 들어있는 글만 — 실제 검색 API 의 거동을 흉내낸다.
        hits = [
            it for it in items
            if not query or query in (it.get("text") or "") or query in (it.get("tags") or "")
        ][:limit]

        posts = [
            RawPost(
                platform=it.get("platform", self.platform),
                post_id=str(it.get("id")),
                text=it.get("text") or "",
                permalink=it.get("permalink") or "",
                author=it.get("author"),
                posted_at=it.get("posted_at"),
                media_type=it.get("media_type") or "TEXT_POST",
                query=query,
                query_mode=mode,
                api_fields_missing=list(it.get("api_fields_missing") or []),
            )
            for it in hits
        ]
        status = STATUS_OK if posts else STATUS_NO_RESULTS
        return CollectResult(status, posts=posts, quota_key=self.quota_key(query, mode))
