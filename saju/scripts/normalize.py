"""게시물 본문 정규화 (한국어 소셜 특화). 전부 순수 함수 — 단위 테스트 가능.

정규화의 목적은 두 가지다.
1) 지식 코퍼스로 쓸 수 있게 잡음(해시태그 더미/이모지/영업 문구)을 걷어낸다.
2) 계정 간 복붙을 같은 해시로 묶는다. 사주 콘텐츠는 같은 본문에 홍보 꼬리만
   바꿔 다는 경우가 매우 흔해서, 정규화 '후'에 해싱해야 중복이 잡힌다.
"""
import hashlib
import re
import unicodedata

# 제로폭/특수 공백. iOS 공유나 붙여넣기로 자주 섞여 들어온다.
_ZERO_WIDTH = dict.fromkeys(
    [0x200B, 0x200C, 0x200D, 0xFEFF, 0x2060, 0x00AD], None
)
_ODD_SPACE = re.compile(r"[  -   　]")

# 이모지 대략 범위. 라이브러리 없이 stdlib 만으로 처리하기 위한 근사치이며,
# 코퍼스 용도상 과하게 지워도(문자 몇 개 손실) 부족하게 지우는 것보다 낫다.
_EMOJI_RANGES = (
    (0x1F300, 0x1FAFF), (0x1F000, 0x1F2FF), (0x2600, 0x27BF),
    (0x2B00, 0x2BFF), (0xFE00, 0xFE0F), (0x1F1E6, 0x1F1FF),
    (0x2190, 0x21FF), (0x2300, 0x23FF),
)

_HASHTAG = re.compile(r"#[^\s#]+")


def _is_emoji(ch):
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _EMOJI_RANGES)


def to_nfc(text):
    """유니코드 NFC 정규화.

    macOS/iOS 에서 올라온 한글은 NFD(자모 분리)로 도착하는 경우가 있다.
    눈으로는 같아 보여도 코드포인트가 달라 해시가 갈리고 용어 매칭도 실패하므로,
    파이프라인 맨 앞에서 반드시 NFC 로 모아준다.
    """
    return unicodedata.normalize("NFC", text or "")


def strip_invisible(text):
    """제로폭 문자 제거 + 특수 공백을 보통 공백으로."""
    return _ODD_SPACE.sub(" ", (text or "").translate(_ZERO_WIDTH))


def collapse_emoji_runs(text):
    """이모지(연속 포함)를 공백 하나로 치환.

    이모지를 남겨두면 같은 본문인데 장식만 다른 글이 다른 해시를 갖게 된다.
    지식 코퍼스에서 이모지가 갖는 정보량도 거의 없어 통째로 걷어낸다.
    """
    out = []
    prev_dropped = False
    for ch in text or "":
        if _is_emoji(ch):
            if not prev_dropped:
                out.append(" ")
            prev_dropped = True
        else:
            out.append(ch)
            prev_dropped = False
    return "".join(out)


def split_trailing_hashtags(text):
    """말미 해시태그 블록을 본문에서 떼어낸다 -> (본문, 해시태그 목록).

    한국 사주 게시물은 끝에 '#사주 #오늘의운세 #신점 ...' 을 20~30개씩 붙인다.
    본문 '중간'에 쓰인 해시태그는 문장의 일부이므로 남긴다.
    """
    lines = (text or "").split("\n")
    tags = []
    # 뒤에서부터, 해시태그가 지배적인 줄을 걷어낸다.
    while lines:
        line = lines[-1].strip()
        if not line:
            lines.pop()
            continue
        found = _HASHTAG.findall(line)
        rest = _HASHTAG.sub(" ", line).strip()
        # 해시태그를 빼고 남는 게 거의 없으면 태그 전용 줄로 본다.
        if found and len(rest) <= 2:
            tags = found + tags
            lines.pop()
            continue
        break
    body = "\n".join(lines)
    # 마지막 줄 끝에 매달린 태그도 정리 (본문 뒤 같은 줄에 이어붙인 경우)
    trailing = re.compile(r"(?:\s*#[^\s#]+)+\s*$")
    m = trailing.search(body)
    if m:
        tags = _HASHTAG.findall(m.group(0)) + tags
        body = body[: m.start()]
    # 본문 '중간'의 해시태그는 문장의 일부다. 한국 사주 글은 '#정관 이 강하면…'
    # 처럼 해시태그를 그냥 명사로 쓰는 일이 잦아, 토큰째 지우면 문장이 부서진다.
    # 따라서 # 기호만 떼고 낱말은 남긴다.
    body = re.sub(r"#(?=[^\s#])", "", body)
    return body.strip(), [t.lstrip("#") for t in tags]


# ---- 영업/홍보 문구 ----
# 한국 사주 소셜의 상수. 문구 자체를 본문에서 걷어내되, 어떤 종류였는지는
# promo_flags 로 남겨 품질 점수에서 감점 근거로 쓴다.
PROMO_PATTERNS = (
    ("dm_solicit", ("dm주세요", "dm 주세요", "dm문의", "dm 문의", "디엠주세요",
                    "디엠 주세요", "디엠으로", "쪽지주세요", "쪽지 주세요")),
    ("consult_solicit", ("상담문의", "상담 문의", "상담신청", "상담 신청",
                         "예약문의", "예약 문의", "상담예약", "상담 예약",
                         "문의는", "신청은", "상담 원하시는")),
    ("profile_link", ("프로필링크", "프로필 링크", "프로필의 링크", "링크클릭",
                      "링크 클릭", "바이오 링크", "링크는 프로필", "프로필에 링크")),
    ("messenger", ("카톡", "카카오톡", "오픈채팅", "오픈카톡", "톡주세요", "톡 주세요")),
    ("price_promo", ("할인가", "이벤트가", "무료상담", "무료 상담", "선착순",
                     "특가", "마감임박", "오픈기념")),
)


def detect_promo(text):
    """본문에서 발견된 영업 문구 종류 목록 (중복 없이, 정의 순서 유지)."""
    low = (text or "").lower()
    flags = []
    for flag, needles in PROMO_PATTERNS:
        if any(n in low for n in needles):
            flags.append(flag)
    return flags


def strip_promo_lines(text):
    """영업 문구가 들어간 줄을 통째로 제거."""
    kept = []
    for line in (text or "").split("\n"):
        if detect_promo(line):
            continue
        kept.append(line)
    return "\n".join(kept)


def collapse_whitespace(text):
    """줄 안의 공백은 하나로, 빈 줄 연속은 하나로, 앞뒤 공백 제거."""
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in (text or "").split("\n")]
    out = []
    for ln in lines:
        if not ln and (not out or not out[-1]):
            continue
        out.append(ln)
    return "\n".join(out).strip()


def normalize(text):
    """전체 정규화 파이프라인.

    반환: {"text_clean", "hashtags", "promo_flags"}
    promo_flags 는 '제거하기 전' 원문 기준으로 잡는다 (지운 뒤엔 못 찾으므로).
    """
    t = to_nfc(text)
    t = strip_invisible(t)
    t = collapse_emoji_runs(t)
    body, tags = split_trailing_hashtags(t)
    promo_flags = detect_promo(body)
    body = strip_promo_lines(body)
    body = collapse_whitespace(body)
    return {"text_clean": body, "hashtags": tags, "promo_flags": promo_flags}


def content_hash(text_clean):
    """정규화된 본문의 내용 해시 (복붙 탐지용).

    해시 직전에 공백을 전부 제거해, 줄바꿈 위치만 다른 복사본도 같은 값이 되게 한다.
    """
    squeezed = re.sub(r"\s+", "", text_clean or "")
    return hashlib.sha256(squeezed.encode("utf-8")).hexdigest()[:16]
