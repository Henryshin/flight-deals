"""중복 표시 (삭제가 아니라 '표시'). 순수 함수 — 파일 I/O 없음.

두 단계로 잡는다.
1) uid  ({platform}:{post_id}) — 같은 글을 여러 런에서 다시 가져온 경우.
   Instagram top_media 는 매번 같은 인기글을 돌려주므로 이게 없으면 코퍼스가
   거의 재탕으로 채워진다. merge=union 때문에 원장에 같은 줄이 두 번 들어갈 수도
   있어, 읽는 쪽에서 반드시 걸러야 한다.
2) content_hash — 다른 계정이 본문을 그대로 복붙한 경우. 사주 콘텐츠는 잘 쓴 해설
   하나가 수십 계정에 복제된다. 정규화 '후' 해싱하므로 홍보 꼬리만 다른 복사본이
   같은 해시로 묶인다.

'삭제하지 않고 표시만' 하는 이유:
- 쿼터(7일 500쿼리)상 지운 데이터는 되살릴 수 없다.
- "같은 해설을 20개 계정이 올렸다"는 사실 자체가 그 내용이 통설이라는 신호라,
  지식 코퍼스의 순위 산정에 쓸모가 있다.
"""

# posted_at 이 없는 글을 정렬 끝으로 보내기 위한 상수 (ISO 문자열 비교라 'Z' 보다 큰 값)
_NO_DATE = "~"


def _canonical_sort_key(rec):
    """대표 레코드 선정 기준: 가장 먼저 올라온 글, 동률이면 uid 사전순.

    결정적이어야 한다 — 입력 순서가 바뀌어도 같은 대표가 나와야 corpus.jsonl 이
    재빌드마다 흔들리지 않고, 워크플로가 의미 없는 커밋을 남기지 않는다.
    """
    return (rec.get("posted_at") or _NO_DATE, rec.get("uid") or "")


def dedupe_by_uid(records):
    """uid 중복 제거. 먼저 나온 것을 남긴다 (append 순서 = 수집 순서)."""
    seen = set()
    out = []
    for rec in records:
        uid = rec.get("uid")
        if not uid or uid in seen:
            continue
        seen.add(uid)
        out.append(rec)
    return out


def mark_duplicates(records):
    """content_hash 가 같은 묶음마다 대표를 정하고 dup_of / is_canonical 을 채운다.

    입력 레코드를 변형하지 않고 얕은 복사본을 돌려준다. 반환 길이 == 입력 길이.
    """
    groups = {}
    for rec in records:
        groups.setdefault(rec.get("content_hash"), []).append(rec)

    canonical_uid = {}
    for chash, group in groups.items():
        winner = min(group, key=_canonical_sort_key)
        canonical_uid[chash] = winner.get("uid")

    out = []
    for rec in records:
        copy = dict(rec)
        rep = canonical_uid.get(rec.get("content_hash"))
        is_canonical = rep == rec.get("uid")
        copy["is_canonical"] = is_canonical
        copy["dup_of"] = None if is_canonical else rep
        out.append(copy)
    return out


def cluster_sizes(records):
    """content_hash 별 복제 개수. 통계/순위용."""
    sizes = {}
    for rec in records:
        chash = rec.get("content_hash")
        sizes[chash] = sizes.get(chash, 0) + 1
    return sizes
