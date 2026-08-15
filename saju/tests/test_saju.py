"""사주 코퍼스 파이프라인 스모크 테스트 (의존성 없음, 네트워크 없음, 자격증명 없음).

실행: python saju/tests/test_saju.py

tests/test_smoke.py 와 같은 손수 만든 러너 방식이되 한 가지만 다르다 —
본가 러너는 AssertionError 만 잡아서 다른 예외가 나면 나머지 테스트 결과를 못 본다.
여기는 JSON 파싱/임시파일 I/O/가짜 transport 배선이 섞여 TypeError 류가 날 여지가
있으므로 Exception 을 모두 잡고 계속 진행한다.

saju 테스트를 tests/test_smoke.py 에 합치지 않는 이유: saju 패키지에서 임포트 오류가
나면 지금 초록불인 항공권 테스트까지 통째로 죽는다.
"""
import json
import sys
import tempfile
import traceback
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from saju.collectors import get_collector  # noqa: E402
from saju.collectors._http import _classify_http_error  # noqa: E402
from saju.collectors.base import (  # noqa: E402
    STATUS_AUTH, STATUS_ERROR, STATUS_OK, STATUS_PARSE, STATUS_PERMISSION,
    STATUS_RATE_LIMITED,
)
from saju.collectors.instagram import InstagramCollector  # noqa: E402
from saju.collectors.threads import ThreadsCollector  # noqa: E402
from saju.scripts import quota  # noqa: E402
from saju.scripts.build_corpus import analyze, build_stats  # noqa: E402
from saju.scripts.dedup import dedupe_by_uid, mark_duplicates  # noqa: E402
from saju.scripts.normalize import (  # noqa: E402
    content_hash, detect_promo, normalize, split_trailing_hashtags, to_nfc,
)
from saju.scripts.quality import label_post, score_post  # noqa: E402
from saju.scripts.terms import tag_terms  # noqa: E402

FIXTURES = ROOT / "saju" / "fixtures" / "sample_posts.json"
KEYWORDS = ROOT / "saju" / "data" / "keywords.json"


def _fixtures():
    return json.loads(FIXTURES.read_text(encoding="utf-8"))["posts"]


def _by_id(pid):
    for p in _fixtures():
        if p["id"] == pid:
            return p
    raise AssertionError(f"픽스처에 {pid} 없음")


# ---------- 정규화 ----------

def test_nfc_normalization():
    """NFD(자모 분리) 한글이 NFC 로 모여야 용어 매칭과 해시가 성립한다."""
    composed = "정관이 강한 사주"
    decomposed = unicodedata.normalize("NFD", composed)
    assert composed != decomposed, "테스트 전제 실패: NFD 변환이 안 됨"
    assert "정관" not in decomposed, "테스트 전제 실패: NFD 인데 부분문자열이 잡힘"

    fixed = to_nfc(decomposed)
    assert "정관" in fixed, "NFC 정규화 후에도 용어가 안 잡힘"
    assert content_hash(fixed) == content_hash(composed), "NFD/NFC 해시가 갈림"


def test_zero_width_stripped_before_promo_match():
    """제로폭 문자로 필터를 피하려는 홍보 문구도 잡아야 한다."""
    obfuscated = "상​담​문​의 는 아래로"
    assert not detect_promo(obfuscated), "테스트 전제 실패: 원문에서 이미 잡힘"
    result = normalize(obfuscated)
    assert "consult_solicit" in result["promo_flags"], (
        f"제로폭 제거 후에도 홍보 문구 미탐지: {result}"
    )


def test_strip_trailing_hashtags_keeps_inline_words():
    """말미 태그 블록은 지우되, 문장 속 해시태그는 낱말로 남긴다."""
    text = "#정관 이 강하면 조직에 잘 맞습니다.\n\n#사주 #십신 #명리학"
    body, tags = split_trailing_hashtags(text)
    assert "정관 이 강하면" in body, f"본문 중간 해시태그가 부서짐: {body!r}"
    assert "#" not in body, f"본문에 # 기호가 남음: {body!r}"
    assert "사주" in tags and "십신" in tags, f"말미 태그 미수집: {tags}"
    assert "명리학" not in body, "말미 태그 블록이 본문에 남음"


def test_emoji_runs_collapsed():
    result = normalize("✨✨✨ 편관 설명 🔮🔮")
    assert "✨" not in result["text_clean"] and "🔮" not in result["text_clean"], (
        f"이모지가 남음: {result['text_clean']!r}"
    )
    assert "편관 설명" in result["text_clean"]


def test_content_hash_ignores_decoration():
    """본문이 같고 이모지/홍보꼬리/해시태그만 다른 복붙은 같은 해시여야 한다."""
    a = normalize(_by_id("1001")["text"])
    b = normalize(_by_id("1008")["text"])
    assert a["text_clean"] == b["text_clean"], (
        "정규화 후 본문이 달라짐:\n"
        f"--- 1001 ---\n{a['text_clean']!r}\n--- 1008 ---\n{b['text_clean']!r}"
    )
    assert content_hash(a["text_clean"]) == content_hash(b["text_clean"])


def test_content_hash_differs_for_different_body():
    a = normalize(_by_id("1001")["text"])
    c = normalize(_by_id("1013")["text"])
    assert content_hash(a["text_clean"]) != content_hash(c["text_clean"])


def test_promo_flags_detected():
    result = normalize(_by_id("1003")["text"])
    assert "consult_solicit" in result["promo_flags"], result["promo_flags"]
    assert "profile_link" in result["promo_flags"], result["promo_flags"]
    assert "상담 문의" not in result["text_clean"], "홍보 줄이 본문에 남음"
    assert "도화살은 본래" in result["text_clean"], "설명 본문까지 지워짐"


# ---------- 용어 태깅 ----------

def test_term_tagging_multichar():
    terms, groups = tag_terms(_by_id("1001")["text"])
    for expected in ("편관", "정관", "칠살", "일간"):
        assert expected in terms, f"{expected} 미태깅: {terms}"
    assert "십신" in groups and "구조" in groups, groups


def test_term_single_char_requires_context():
    """'목요일/금요일' 이 오행으로 오탐되면 안 된다 — 가장 흔한 실패 모드."""
    terms, groups = tag_terms(_by_id("1009")["text"])
    for bad in ("목", "화", "토", "금", "수"):
        assert bad not in terms, f"일상어에서 오행 '{bad}' 오탐: {terms}"
    assert not groups, f"사주와 무관한 글에 그룹이 붙음: {groups}"


def test_term_single_char_matches_with_context():
    terms, _ = tag_terms("목 기운이 강한 사람은 성장 욕구가 큽니다.")
    assert "목" in terms, f"문맥이 있는데 오행 목 미태깅: {terms}"


def test_term_sanggwan_ignores_common_word():
    """'상관없다/상관관계' 는 십신 상관이 아니다."""
    terms, _ = tag_terms("그건 저와 상관없는 일입니다. 상관관계도 없어요.")
    assert "상관" not in terms, f"'상관없다'를 십신으로 오탐: {terms}"
    terms2, _ = tag_terms("상관이 강하면 표현 욕구가 큽니다.")
    assert "상관" in terms2, f"진짜 십신 상관을 놓침: {terms2}"


# ---------- 품질/라벨 ----------

def test_quality_promo_scores_lower_than_knowledge():
    def s(pid):
        n = normalize(_by_id(pid)["text"])
        t, g = tag_terms(n["text_clean"])
        return score_post(n["text_clean"], t, g, n["promo_flags"], n["hashtags"])
    assert s("1001") > s("1008"), "같은 본문인데 홍보 꼬리가 붙은 쪽이 더 높음"
    assert s("1001") > s("1004"), "해설글이 광고글보다 낮음"


def test_labels_match_fixture_expectations():
    """골든 테스트 — 픽스처의 expected_label 을 그대로 재현해야 한다."""
    wrong = []
    for post in _fixtures():
        rec = analyze({
            "uid": f"sample:{post['id']}",
            "text_raw": post["text"],
            "media_type": post.get("media_type", ""),
        })
        if rec["label"] != post["expected_label"]:
            wrong.append(
                f"{post['id']}: 기대 {post['expected_label']} / 실제 {rec['label']}"
                f" (점수 {rec['quality']}, 홍보 {rec['promo_flags']})"
            )
    assert not wrong, "라벨 불일치:\n  " + "\n  ".join(wrong)


def test_quality_monotonic_with_promo():
    base = "편관은 나를 극하는 오행입니다. 일간이 신강하면 편관이 자산이 됩니다. 정관과 비교해 보면 압박에 가깝습니다."
    n1 = normalize(base)
    n2 = normalize(base + "\n상담 문의는 프로필 링크로 주세요")
    t1, g1 = tag_terms(n1["text_clean"])
    t2, g2 = tag_terms(n2["text_clean"])
    s1 = score_post(n1["text_clean"], t1, g1, n1["promo_flags"], n1["hashtags"])
    s2 = score_post(n2["text_clean"], t2, g2, n2["promo_flags"], n2["hashtags"])
    assert s2 < s1, f"홍보 문구를 붙였는데 점수가 안 내려감 ({s1} -> {s2})"


# ---------- 중복 ----------

def test_dedupe_by_uid_absorbs_union_merge_duplicates():
    """merge=union 으로 같은 줄이 두 번 들어가도 코퍼스는 1건이어야 한다."""
    row = {"uid": "sample:1", "text_raw": "편관 설명"}
    assert len(dedupe_by_uid([row, dict(row), dict(row)])) == 1


def test_mark_duplicates_is_non_destructive_and_deterministic():
    recs = [
        {"uid": "s:b", "content_hash": "h1", "posted_at": "2026-08-14T00:00:00Z"},
        {"uid": "s:a", "content_hash": "h1", "posted_at": "2026-08-10T00:00:00Z"},
        {"uid": "s:c", "content_hash": "h2", "posted_at": "2026-08-12T00:00:00Z"},
    ]
    out = mark_duplicates(recs)
    assert len(out) == len(recs), "중복이 삭제됨 (표시만 해야 함)"

    canon = {r["uid"] for r in out if r["is_canonical"]}
    assert canon == {"s:a", "s:c"}, f"대표 선정이 틀림: {canon}"
    dup = [r for r in out if not r["is_canonical"]][0]
    assert dup["dup_of"] == "s:a", dup

    # 입력 순서를 뒤집어도 같은 대표가 나와야 재빌드 때 커밋 노이즈가 없다.
    reversed_out = mark_duplicates(list(reversed(recs)))
    assert {r["uid"] for r in reversed_out if r["is_canonical"]} == canon, (
        "입력 순서에 따라 대표가 바뀜 — 재빌드마다 corpus.jsonl 이 흔들린다"
    )


def test_repost_is_marked_duplicate_of_original():
    recs = []
    for pid in ("1001", "1008"):
        post = _by_id(pid)
        recs.append(analyze({
            "uid": f"sample:{pid}",
            "text_raw": post["text"],
            "posted_at": post["posted_at"],
            "media_type": post.get("media_type", ""),
        }))
    out = mark_duplicates(recs)
    canon = [r for r in out if r["is_canonical"]]
    assert len(canon) == 1, f"복붙 두 건이 각각 대표가 됨: {[r['uid'] for r in canon]}"
    assert canon[0]["uid"] == "sample:1001", "더 먼저 올라온 글이 대표여야 함"


# ---------- 쿼터 원장 ----------

def _tmp_ledger():
    return Path(tempfile.mkdtemp()) / "quota.jsonl"


def test_quota_threads_counts_every_call():
    path = _tmp_ledger()
    now = datetime(2026, 8, 15, tzinfo=timezone.utc)
    for i in range(500):
        quota.record("threads", f"keyword:q{i}", path=path, now=now)
    ok, why = quota.has_budget("threads", "keyword:new", path=path, now=now)
    assert not ok and "소진" in why, f"500건인데 허용됨: {why}"

    # 8일 뒤에는 윈도우가 지나 다시 가능해야 한다.
    later = now + timedelta(days=8)
    ok2, _ = quota.has_budget("threads", "keyword:new", path=path, now=later)
    assert ok2, "롤링 윈도우가 지났는데도 막힘"


def test_quota_instagram_counts_unique_tags_not_calls():
    """Instagram 한도는 '고유 해시태그 수'다. 호출당 과금으로 구현하면 안 된다."""
    path = _tmp_ledger()
    now = datetime(2026, 8, 15, tzinfo=timezone.utc)
    for _ in range(50):
        quota.record("instagram", "hashtag:사주", path=path, now=now)
    assert quota.usage("instagram", path=path, now=now) == 1, "같은 태그 반복이 여러 개로 셈"
    ok, _ = quota.has_budget("instagram", "hashtag:사주", path=path, now=now)
    assert ok, "이미 쓴 태그 재조회가 막힘 (공짜여야 함)"

    for i in range(29):
        quota.record("instagram", f"hashtag:t{i}", path=path, now=now)
    assert quota.usage("instagram", path=path, now=now) == 30
    ok2, why = quota.has_budget("instagram", "hashtag:새태그", path=path, now=now)
    assert not ok2 and "해시태그" in why, f"31번째 고유 태그가 허용됨: {why}"


def test_quota_hard_block_overrides_counter():
    path = _tmp_ledger()
    now = datetime(2026, 8, 15, tzinfo=timezone.utc)
    ok, _ = quota.has_budget("threads", "keyword:q", path=path, now=now)
    assert ok, "빈 원장인데 막힘"

    quota.mark_hard_block("threads", "API가 쿼터 초과 응답", path=path, now=now)
    ok2, why = quota.has_budget("threads", "keyword:q", path=path, now=now)
    assert not ok2 and "차단" in why, f"hard_block 이 무시됨: {why}"

    ok3, _ = quota.has_budget("threads", "keyword:q", path=path, now=now + timedelta(hours=25))
    assert ok3, "hard_block 이 자동 해제되지 않음"


def test_quota_prune_keeps_window_and_active_blocks():
    path = _tmp_ledger()
    now = datetime(2026, 8, 15, tzinfo=timezone.utc)
    quota.record("threads", "keyword:old", path=path, now=now - timedelta(days=30))
    quota.record("threads", "keyword:new", path=path, now=now)
    quota.mark_hard_block("threads", "차단", path=path, now=now)

    kept = quota.prune(path=path, now=now)
    assert kept == 2, f"오래된 항목만 지워야 하는데 {kept}건 남음"
    assert quota.usage("threads", path=path, now=now) == 1
    assert quota.hard_blocked_until("threads", path=path, now=now) is not None, (
        "유효한 hard_block 이 prune 에 지워짐"
    )


# ---------- 수집기 ----------

def test_sample_collector_end_to_end():
    c = get_collector("sample")
    ok, why = c.available()
    assert ok, why
    result = c.search("편관", limit=10)
    assert result.status == STATUS_OK, f"{result.status}: {result.detail}"
    assert result.posts, "픽스처에서 편관 글을 못 찾음"
    for p in result.posts:
        assert p.uid.startswith("sample:"), p.uid
        assert p.text, "본문이 빈 레코드"


def test_collectors_unavailable_without_token():
    """토큰이 없을 때 크래시가 아니라 STATUS_AUTH 로 깔끔히 끝나야 한다 (cron 안전)."""
    for collector in (ThreadsCollector(token=""), InstagramCollector(token="", user_id="")):
        ok, why = collector.available()
        assert not ok and "미설정" in why, why
        result = collector.search("사주")
        assert result.status == STATUS_AUTH, result.status


def test_threads_field_mapping_with_fake_transport():
    """토큰 없이도 실제 수집기의 필드 매핑을 검증한다 (_transport 주입)."""
    payload = {"data": [{
        "id": "17912345678901234",
        "text": "편관은 나를 극하는 오행입니다.",
        "permalink": "https://www.threads.net/@x/post/ABC",
        "timestamp": "2026-08-14T03:22:11+0000",
        "username": "x",
        "media_type": "TEXT_POST",
    }]}
    calls = []

    def fake(url, params):
        calls.append((url, params))
        return STATUS_OK, payload, ""

    c = ThreadsCollector(token="fake-token", _transport=fake)
    result = c.search("편관", limit=5)
    assert result.status == STATUS_OK, result.detail
    post = result.posts[0]
    assert post.uid == "threads:17912345678901234", post.uid
    assert post.author == "x" and post.posted_at.startswith("2026-08-14")
    assert post.permalink.endswith("/ABC")
    assert calls[0][1]["q"] == "편관", calls[0][1]


def test_threads_tag_mode_sets_search_mode():
    captured = {}

    def fake(url, params):
        captured.update(params)
        return STATUS_OK, {"data": []}, ""

    ThreadsCollector(token="t", _transport=fake).search("사주", mode="tag")
    assert captured.get("search_mode") == "TAG", captured


def test_threads_missing_data_field_is_parse_error():
    def fake(url, params):
        return STATUS_OK, {"unexpected": 1}, ""

    result = ThreadsCollector(token="t", _transport=fake).search("사주")
    assert result.status == STATUS_PARSE, result.status


def test_instagram_two_step_null_author_and_id_cache():
    cache = Path(tempfile.mkdtemp()) / "hashtag_ids.json"
    urls = []

    def fake(url, params):
        urls.append(url)
        if url.endswith("ig_hashtag_search"):
            return STATUS_OK, {"data": [{"id": "17843000000"}]}, ""
        return STATUS_OK, {"data": [{
            "id": "18000000000",
            "caption": "지장간이란 지지 속에 숨은 천간입니다.",
            "permalink": "https://www.instagram.com/p/XYZ/",
            "timestamp": "2026-08-14T01:00:00+0000",
            "media_type": "IMAGE",
        }]}, ""

    c = InstagramCollector(token="t", user_id="u", cache_path=cache, _transport=fake)
    result = c.search("사주")
    assert result.status == STATUS_OK, result.detail
    assert urls[0].endswith("ig_hashtag_search"), f"태그 ID 조회가 먼저여야 함: {urls}"
    assert "recent_media" in urls[1], urls

    post = result.posts[0]
    assert post.author is None, "IG 해시태그 API 는 작성자를 주지 않는다"
    assert "author" in post.api_fields_missing, post.api_fields_missing
    assert result.quota_key == "hashtag:사주", result.quota_key

    # 두 번째 호출은 캐시를 써서 태그 ID 조회를 건너뛰어야 한다.
    urls.clear()
    c.search("사주")
    assert not any(u.endswith("ig_hashtag_search") for u in urls), (
        f"해시태그 ID 캐시가 동작하지 않음: {urls}"
    )


def test_error_classification():
    """앱 심사 전 정상 상태(권한 미승인)와 토큰 고장을 반드시 구분해야 한다."""
    cases = [
        (400, {"error": {"code": 190, "message": "expired"}}, STATUS_AUTH),
        (400, {"error": {"code": 10, "message": "no permission"}}, STATUS_PERMISSION),
        (400, {"error": {"code": 200, "message": "scope"}}, STATUS_PERMISSION),
        (400, {"error": {"code": 4, "message": "too many calls"}}, STATUS_RATE_LIMITED),
        (429, {}, STATUS_RATE_LIMITED),
        (500, {"error": {"code": 1, "message": "oops"}}, STATUS_ERROR),
    ]
    for http_code, body, expected in cases:
        status, _, detail = _classify_http_error(http_code, json.dumps(body))
        assert status == expected, f"HTTP {http_code} {body} -> {status} (기대 {expected})"
        assert detail, "사유 문자열이 비어 있음"


# ---------- 저장 형식 / 설정 파일 ----------

def test_jsonl_record_is_single_line():
    """union merge 안전성의 근거 — 레코드 하나가 반드시 한 줄이어야 한다."""
    rec = analyze({
        "uid": "sample:1001",
        "text_raw": _by_id("1001")["text"],   # 줄바꿈이 잔뜩 들어있는 본문
        "media_type": "TEXT_POST",
    })
    line = json.dumps(rec, ensure_ascii=False, sort_keys=True)
    assert "\n" not in line, "직렬화 결과에 실제 줄바꿈이 있음 — union merge 시 레코드가 쪼개진다"
    assert json.loads(line) == rec, "라운드트립 실패"


def test_build_stats_is_deterministic():
    recs = mark_duplicates([
        analyze({
            "uid": f"sample:{p['id']}",
            "text_raw": p["text"],
            "posted_at": p["posted_at"],
            "platform": "sample",
            "media_type": p.get("media_type", ""),
        })
        for p in _fixtures()
    ])
    a = json.dumps(build_stats(recs), ensure_ascii=False, sort_keys=True)
    b = json.dumps(build_stats(list(reversed(recs))), ensure_ascii=False, sort_keys=True)
    assert a == b, "입력 순서에 따라 통계가 달라짐 (재빌드마다 커밋 노이즈 발생)"
    assert "built_at" not in a, "타임스탬프가 들어가면 매 빌드 diff 가 난다"


def test_keywords_json_wellformed():
    data = json.loads(KEYWORDS.read_text(encoding="utf-8"))
    queries = data.get("queries")
    assert queries, "queries 가 비어 있음"
    seen = set()
    for q in queries:
        assert q.get("value"), f"value 없음: {q}"
        assert q.get("mode") in ("keyword", "tag", "hashtag"), f"잘못된 mode: {q}"
        assert isinstance(q.get("priority"), int), f"priority 는 정수여야 함: {q}"
        key = (q["value"], q["mode"])
        assert key not in seen, f"중복 검색어: {key}"
        seen.add(key)
    # Instagram 은 7일 롤링 고유 해시태그 30개가 한도다.
    tags = [q for q in queries if q["mode"] == "hashtag"]
    assert len(tags) <= 30, f"hashtag 항목이 {len(tags)}개 — 7일 한도 30개를 넘음"


def test_fixtures_wellformed():
    posts = _fixtures()
    assert len(posts) >= 10, f"픽스처가 {len(posts)}건뿐 — 라벨 커버리지가 부족"
    ids = [p["id"] for p in posts]
    assert len(ids) == len(set(ids)), "픽스처 id 중복"
    labels = {p["expected_label"] for p in posts}
    for needed in ("knowledge", "mixed", "promo", "fortune_feed", "question", "thin"):
        assert needed in labels, f"픽스처에 {needed} 사례가 없음"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
        except Exception:
            failed += 1
            last = traceback.format_exc().strip().split("\n")[-1]
            print(f"ERROR {t.__name__}: {last}")
    if failed:
        print(f"{failed}/{len(tests)}개 실패")
        sys.exit(1)
    print(f"{len(tests)}개 테스트 통과")


if __name__ == "__main__":
    main()
