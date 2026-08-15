"""'설명하는 글' vs '광고하는 글' 품질 점수 + 라벨.

한국 사주 소셜 게시물의 대다수는 지식이 아니다. 그런데 '광고'만 걸러내면 부족하다 —
'사주' 로 검색하면 다음 두 부류가 대량으로 딸려오는데, 둘 다 지식도 광고도 아니다.
- 질문글  : "제 사주 좀 봐주세요 95년생 ..." (사주 풀이 요청)
- 운세피드: "오늘의 띠별 운세" (자동 생성 운세 나열)
점수 하나로는 이들이 '애매한 중간값'으로 뭉개지므로 라벨을 따로 둔다.

중요: 여기서 '걸러내지' 않는다. 임계값을 정하려면 아직 없는 실데이터가 필요하고,
쿼터(7일 500쿼리)를 생각하면 수집 시점의 삭제는 되돌릴 수 없다.
posts.jsonl 은 전부 보존하고, build_corpus.py 가 임계값 이상만 담은 '뷰'를 만든다.
"""
import re

LABEL_KNOWLEDGE = "knowledge"     # 용어 설명/해석 본문
LABEL_MIXED = "mixed"             # 설명 + 말미 홍보 (실무상 가장 흔한 형태)
LABEL_PROMO = "promo"             # 영업글
LABEL_FORTUNE = "fortune_feed"    # 오늘의 운세류 자동 생성 피드
LABEL_QUESTION = "question"       # 봐주세요 요청글
LABEL_THIN = "thin"               # 이미지 캡션/한 줄

# 설명형 글에서 나타나는 표현. 개수가 많을수록 해설일 가능성이 높다.
EXPLAIN_MARKERS = (
    "이란", "라는 뜻", "라는 의미", "의미합니다", "의미해", "뜻합니다", "뜻해",
    "예를 들어", "때문입니다", "때문에", "왜냐하면", "말합니다", "봅니다",
    "해석합니다", "나타냅니다", "가리킵니다", "라고 합니다", "특징은", "차이는",
    "정리하면", "구분하면", "반대로", "이 경우",
)

FORTUNE_MARKERS = (
    "오늘의 운세", "오늘의운세", "이번주 운세", "주간 운세", "띠별 운세",
    "띠별운세", "별자리 운세", "내일의 운세", "월간 운세", "오늘의 띠별",
)

QUESTION_MARKERS = (
    "봐주세요", "봐주실", "봐주시", "여쭤", "여쭙", "궁금합니다", "궁금해요",
    "알려주세요", "해석 부탁", "풀이 부탁", "조언 부탁",
)

# 캡션이 이만큼도 안 되는 이미지/영상 글은 지식이 이미지 안에 있다 — 텍스트 코퍼스엔 무의미.
MEDIA_TYPES_VISUAL = ("IMAGE", "VIDEO", "CAROUSEL_ALBUM")
MEDIA_ONLY_CHARS = 80

_SENTENCE_END = re.compile(r"[.!?。]|다\s|요\s|죠\s")

# 각 항목의 만점 기여도 (합계 1.0)
W_LENGTH = 0.25
W_TERMS = 0.25
W_GROUPS = 0.20
W_EXPLAIN = 0.20
W_SENTENCE = 0.10

PENALTY_PER_PROMO = 0.15
PENALTY_HASHTAG_SPAM = 0.15
PENALTY_TOO_SHORT = 0.25
TOO_SHORT_CHARS = 40

# 라벨 경계
KNOWLEDGE_MIN = 0.45
MIXED_MIN = 0.30


def content_score(text_clean, terms, term_groups, hashtags):
    """홍보 감점을 뺀 '본문 자체의 설명력' 점수.

    mixed / promo 판정은 이 값으로 한다. 최종 점수로 판정하면 홍보 감점이 두 번
    반영되어("광고가 붙었으니 감점" + "점수가 낮으니 광고"), 말미에 상담 문의만
    달린 멀쩡한 해설글이 순수 광고로 분류된다.
    """
    text = text_clean or ""
    terms = terms or []
    term_groups = term_groups or []
    hashtags = hashtags or []

    # 1글자 용어는 문맥 규칙을 통과했어도 오탐 여지가 남아 0.5개로 친다.
    weighted_terms = sum(0.5 if len(t) == 1 else 1.0 for t in terms)

    length = min(len(text) / 600.0, 1.0) * W_LENGTH
    term = min(weighted_terms / 6.0, 1.0) * W_TERMS
    group = min(len(term_groups) / 3.0, 1.0) * W_GROUPS

    explain_hits = sum(1 for m in EXPLAIN_MARKERS if m in text)
    explain = min(explain_hits / 3.0, 1.0) * W_EXPLAIN

    sentences = len([s for s in _SENTENCE_END.split(text) if s.strip()])
    sentence = min(sentences / 5.0, 1.0) * W_SENTENCE

    raw = length + term + group + explain + sentence

    penalty = 0.0
    words = len(text.split()) or 1
    if len(hashtags) >= 8 and len(hashtags) / words > 0.4:
        penalty += PENALTY_HASHTAG_SPAM
    if len(text) < TOO_SHORT_CHARS:
        penalty += PENALTY_TOO_SHORT

    return round(max(0.0, min(1.0, raw - penalty)), 3)


def score_post(text_clean, terms, term_groups, promo_flags, hashtags):
    """0.0(광고) ~ 1.0(해설) 사이 최종 점수. 순수 함수."""
    base = content_score(text_clean, terms, term_groups, hashtags)
    penalty = PENALTY_PER_PROMO * len(promo_flags or [])
    return round(max(0.0, min(1.0, base - penalty)), 3)


def label_post(text_clean, score, promo_flags, media_type="", content=None):
    """점수만으로 구분되지 않는 부류를 먼저 걸러낸 뒤 라벨을 매긴다.

    순서가 중요하다 — 운세피드/질문글은 용어가 제법 섞여 있어 점수만 보면
    'knowledge' 로 새기 쉽다.

    content 는 홍보 감점 이전의 본문 설명력 점수. mixed/promo 를 가르는 데 쓴다.
    생략하면 score 를 그대로 쓴다(감점이 두 번 반영되니 호출부에서 넘기는 편이 낫다).
    """
    text = text_clean or ""
    promo_flags = promo_flags or []
    content = score if content is None else content

    if any(m in text for m in FORTUNE_MARKERS):
        return LABEL_FORTUNE
    if any(m in text for m in QUESTION_MARKERS) and len(text) < 400:
        return LABEL_QUESTION
    if promo_flags:
        # 홍보 판정은 길이 검사보다 '먼저' 와야 한다. 순수 광고글은 홍보 줄을
        # 걷어내고 나면 본문이 거의 남지 않아, 길이로 먼저 자르면 전부 thin 이 되어
        # 광고가 코퍼스에서 사라진다 — 분류기 정밀도를 잴 음성 표본을 잃는 셈이다.
        #
        # 홍보 문구가 있어도 본문에 설명이 충분하면 버리지 않는다. 한국 사주 글에서
        # 가장 흔한 형태가 '제대로 된 십신 설명 + 말미에 상담 문의' 이기 때문에,
        # 이분법으로 자르면 실제 지식의 상당수를 잃는다.
        return LABEL_MIXED if content >= MIXED_MIN else LABEL_PROMO
    if len(text) < TOO_SHORT_CHARS:
        return LABEL_THIN
    if (media_type or "").upper() in MEDIA_TYPES_VISUAL and len(text) < MEDIA_ONLY_CHARS:
        # 지식이 이미지 안에 있는 글. 텍스트 코퍼스로는 건질 게 없다.
        return LABEL_THIN
    return LABEL_KNOWLEDGE if score >= KNOWLEDGE_MIN else LABEL_THIN
