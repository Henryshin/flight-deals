"""수집기 공통 계약 (인터페이스 + 상태값).

Threads / Instagram / 샘플 수집기를 같은 계약으로 갈아끼우기 위한 얇은 층.
collector/google_flights_crawler.py 의 STATUS_* 관례를 그대로 따른다 —
"실패"를 예외로 던지지 않고 분류된 상태값으로 돌려주면, 오케스트레이터가
빈 결과의 이유(토큰 없음/권한 미승인/쿼터 소진/결과 없음)를 상태 파일에 남길 수 있다.

_transport 주입 이음새는 PriceCrawlerSession(_playwright_factory=...) 와 같은 목적이다.
이것이 있어야 토큰 없이도 실제 Threads/Instagram 수집기의 필드 매핑과 오류 분류를
픽스처로 단위 테스트할 수 있다.
"""
from dataclasses import dataclass, field

# ---- 수집 결과 상태값 ----
STATUS_OK = "ok"                     # 호출 성공 (posts 가 비어있을 수는 있음)
STATUS_NO_RESULTS = "no_results"     # 검색은 됐으나 결과 0건
STATUS_AUTH = "auth"                 # 토큰 없음/만료 (code 190) — 사람이 고쳐야 함
STATUS_PERMISSION = "permission"     # 토큰은 유효하나 스코프 미승인 (code 10/200).
                                     # threads_keyword_search 앱 심사 전의 '정상 상태'이며
                                     # 토큰 고장(STATUS_AUTH)과 반드시 구분해야 한다.
STATUS_QUOTA = "quota"               # 롤링 윈도우 소진 — 호출 자체를 하지 않음
STATUS_RATE_LIMITED = "rate_limited" # 단기 스로틀 (code 4/17/32) — 재시도 가능
STATUS_TIMEOUT = "timeout"           # 네트워크 타임아웃
STATUS_PARSE = "parse"               # 200 인데 기대한 필드가 없음 (API 구조 변경 의심)
STATUS_ERROR = "error"               # 그 외 예외

# 검색 모드. Threads 는 키워드/토픽태그 둘 다, Instagram 은 해시태그만 지원한다.
MODE_KEYWORD = "keyword"
MODE_TAG = "tag"
MODE_HASHTAG = "hashtag"


@dataclass
class RawPost:
    """플랫폼 응답을 공통 형태로 옮긴 것 (아직 정규화 전).

    Instagram 해시태그 API 는 작성자 정보를 주지 않으므로 author 는 None 이 될 수 있다.
    이때 왜 비었는지를 api_fields_missing 에 남긴다 — 몇 달 뒤에 "이 글이 익명인가,
    수집기가 깨진 건가"를 답할 수 있어야 하기 때문이다.
    """
    platform: str
    post_id: str
    text: str
    permalink: str = ""
    author: str = None
    posted_at: str = None       # ISO8601 UTC 문자열
    media_type: str = ""
    query: str = ""             # 이 글을 찾아낸 검색어 (출처 추적)
    query_mode: str = MODE_KEYWORD
    api_fields_missing: list = field(default_factory=list)

    @property
    def uid(self):
        """{platform}:{post_id} — 재수집 시 정확 중복 제거용 기본키.

        내용 해시를 기본키로 쓰지 않는 이유: 같은 글을 올린 두 계정은 permalink 도
        삭제 시점도 다른 '별개의 글'이다. 쓰기 시점에 합쳐버리면 출처가 사라진다.
        """
        return f"{self.platform}:{self.post_id}"


@dataclass
class CollectResult:
    status: str
    posts: list = field(default_factory=list)
    detail: str = ""            # 실패 사유 사람이 읽을 수 있게
    quota_key: str = ""         # 이 호출이 소비한 쿼터 키

    @property
    def ok(self):
        return self.status == STATUS_OK


class Collector:
    """수집기 베이스. 하위 클래스는 name/platform/available/search 를 채운다."""

    name = "base"
    platform = "base"
    modes = (MODE_KEYWORD,)

    def __init__(self, _transport=None):
        # _transport(url, params) -> (status, payload, detail)
        # 기본값은 실제 HTTP. 테스트는 픽스처를 돌려주는 가짜를 주입한다.
        self._transport = _transport

    def available(self):
        """(가능여부, 한국어 사유). 자격증명이 갖춰져 실제 호출이 가능한지."""
        raise NotImplementedError

    def quota_key(self, query, mode):
        """이 검색이 소비할 쿼터 키. Instagram 은 이 키의 '고유 개수'가 한도다."""
        return f"{mode}:{query}"

    def search(self, query, mode=MODE_KEYWORD, limit=25):
        """검색 1회. 반드시 CollectResult 를 반환하고 예외를 밖으로 내지 않는다."""
        raise NotImplementedError
