"""세션에서 쓴 초안(draft.md)을 posts/ 에 저장하고 가격 카드를 렌더한다.

    python scripts/blog_save.py draft.md
    python scripts/blog_save.py draft.md --dry-run      # 쓰지 않고 검사만
    python scripts/blog_save.py draft.md --no-images    # 카드 렌더 생략

검증에서 걸리면 저장하지 않는다. 특히 **본문에 등장하는 금액이 자료에 없는 값이면
거부한다** — 근거 없는 가격이 블로그로 나가는 것을 기계적으로 막는 장치다.
"""
import argparse
import html
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from blog import brief as B
from blog import data as D
from blog import imagecard as IC
from blog import render as R
from blog import store as S

REQUIRED = ("title", "monitor_id", "window_id")
PRICE_RE = re.compile(r"([0-9]{1,3}(?:,[0-9]{3})+)\s*원")
HYPE = ("강추", "역대급", "무조건", "대박", "실화", "미쳤", "레전드")
MAX_TITLE = 45
MAX_TAGS = 10


class DraftInvalid(Exception):
    pass


def known_prices(mat):
    """자료에서 나온 금액 + 그 금액들의 차이. 본문은 이 집합 안에서만 숫자를 쓴다."""
    vals = set()
    c, h = mat["cell"], mat["history"]
    for v in (c["min_price"], c.get("typical"), c.get("offpeak_baseline"),
              h.get("min_30d"), h.get("avg_30d")):
        if isinstance(v, (int, float)) and v:
            vals.add(int(v))
    for p in [mat["best"], *mat["alternatives"]]:
        if p.get("price"):
            vals.add(int(p["price"]))
    for r in mat.get("airlines") or []:
        vals.add(int(r["price"]))
    if h.get("wow"):
        vals.update({int(h["wow"]["now"]), int(h["wow"]["prev"])})
    # 두 값의 차이도 글에 자주 쓴다 ("21만원 더 싸다")
    diffs = {abs(a - b) for a in vals for b in vals if a != b}
    return vals | {d for d in diffs if d}


def validate(meta, blocks, text, mat):
    errs = []
    for k in REQUIRED:
        if not meta.get(k):
            errs.append(f"머리말에 {k} 가 없다")
    title = meta.get("title", "")
    if len(title) > MAX_TITLE:
        errs.append(f"제목이 {len(title)}자다 (최대 {MAX_TITLE})")
    tags = meta.get("tags") or []
    if not 3 <= len(tags) <= MAX_TAGS:
        errs.append(f"태그가 {len(tags)}개다 (3~{MAX_TAGS}개)")
    for t in tags:
        if " " in t or t.startswith("#"):
            errs.append(f"태그 '{t}': 공백과 # 없이 쓴다")

    bad_tags = R.used_tags(blocks) - R.ALLOWED_TAGS
    if bad_tags:
        errs.append(f"SmartEditor 가 지우는 태그가 있다: {sorted(bad_tags)}")
    if "style=" in "".join(b.get("content", "") for b in blocks):
        errs.append("style 속성은 붙여넣기에서 제거된다. 쓰지 않는다")

    for w in HYPE:
        if w in text:
            errs.append(f"과장 표현 '{w}' 이 들어 있다")

    if not any(b["type"] == "photo" for b in blocks):
        errs.append("[[PHOTO: ...]] 자리가 하나도 없다 — 여행 글에는 사진이 필요하다")

    ok = known_prices(mat)
    for m in PRICE_RE.finditer(text):
        v = int(m.group(1).replace(",", ""))
        if v not in ok:
            errs.append(f"자료에 없는 금액이다: {m.group(1)}원")

    if mat["history"].get("wow") is None and re.search(r"(내렸|떨어졌|하락)", text):
        errs.append("주간 비교 관측이 부족한데 '내렸다'고 썼다")

    if not mat.get("climate") and re.search(r"(우기|건기|기온|습도)", text):
        errs.append("destmeta 에 기후 자료가 없는데 날씨 이야기를 썼다")

    return errs


def build_post(draft_path, today=None, with_images=True):
    text = Path(draft_path).read_text(encoding="utf-8")
    meta, body = R.parse_front_matter(text)
    today = today or datetime.now(D.KST).date()

    inputs = D.load_inputs()
    D.assert_fresh(inputs["meta"])
    mat = B.material(inputs, meta["monitor_id"], meta["window_id"], today)

    blocks = R.parse_body(body)
    plain = R.blocks_to_text(blocks)
    errs = validate(meta, blocks, plain + "\n" + meta.get("title", ""), mat)
    if errs:
        raise DraftInvalid("\n".join(f"  - {e}" for e in errs))

    cards = IC.build_cards(mat, inputs["history"].get(meta["monitor_id"], []))
    wanted = {b["card"] for b in blocks if b["type"] == "card"}
    have = {n for n, _ in cards}
    missing = wanted - have
    if missing:
        raise DraftInvalid(
            f"  - 만들 수 없는 카드를 참조했다: {sorted(missing)} (가능: {sorted(have)})"
        )

    png = {}
    if with_images and wanted:
        specs = [(n, h) for n, h in cards if n in wanted]
        for (n, _), blob in zip(specs, IC.render_html_to_png([h for _, h in specs])):
            png[n] = blob

    slug = S.post_slug(today, meta["monitor_id"], meta["window_id"])
    post = {
        "version": 1,
        "slug": slug,
        "date": today.isoformat(),
        "title": meta["title"],
        "category": meta.get("category", ""),
        "tags": meta.get("tags", []),
        "monitor_id": meta["monitor_id"],
        "window_id": meta["window_id"],
        "dest": mat["dest"],
        "dest_ko": mat["dest_ko"],
        "data_generated_at": mat["data_generated_at"],
        "generated_at": datetime.now(D.KST).isoformat(timespec="seconds"),
        "booking_url": mat["booking_url"],
        "blocks": blocks,
        "photo_hints": [b["hint"] for b in blocks if b["type"] == "photo"],
    }
    return post, png, mat


def write_post(post, png, posts_dir=None):
    d = S.post_dir(post["slug"], posts_dir)
    (d / "images").mkdir(parents=True, exist_ok=True)
    files = {}
    for name, blob in png.items():
        rel = f"images/card-{name}.png"
        (d / rel).write_bytes(blob)
        files[name] = rel
    for b in post["blocks"]:
        if b["type"] == "card":
            b["file"] = files.get(b["card"], f"images/card-{b['card']}.png")

    (d / "post.json").write_text(
        json.dumps(post, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    (d / "body.html").write_text(
        f"<h2>{html.escape(post['title'])}</h2>\n"
        + R.blocks_to_html(post["blocks"], files) + "\n",
        encoding="utf-8",
    )
    (d / "body.txt").write_text(
        post["title"] + "\n\n" + R.blocks_to_text(post["blocks"]) + "\n",
        encoding="utf-8",
    )
    return d


def main(argv=None):
    ap = argparse.ArgumentParser(description="초안을 posts/ 에 저장")
    ap.add_argument("draft", help="초안 마크다운 경로")
    ap.add_argument("--date", help="발행 기준일 YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true", help="쓰지 않고 검사만")
    ap.add_argument("--no-images", action="store_true", help="카드 렌더 생략")
    args = ap.parse_args(argv)

    today = (date.fromisoformat(args.date) if args.date
             else datetime.now(D.KST).date())
    try:
        post, png, mat = build_post(
            args.draft, today, with_images=not (args.no_images or args.dry_run)
        )
    except R.DraftError as e:
        print(f"초안 형식 오류: {e}", file=sys.stderr)
        return 1
    except DraftInvalid as e:
        print(f"검증 실패 — 저장하지 않았다:\n{e}", file=sys.stderr)
        return 1
    except (D.StaleDataError, D.MissingDataError, KeyError, ValueError) as e:
        print(f"자료 오류: {e}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"OK  제목: {post['title']}")
        print(f"    슬러그: {post['slug']}")
        print(f"    태그: {' '.join(post['tags'])}")
        print(f"    블록: {len(post['blocks'])}개 "
              f"(사진 자리 {len(post['photo_hints'])}곳)")
        print(f"    카드: {sorted({b['card'] for b in post['blocks'] if b['type'] == 'card'})}")
        print("\n--- 평문 미리보기 ---")
        print(R.blocks_to_text(post["blocks"])[:1200])
        return 0

    d = write_post(post, png)
    ledger = S.load_ledger()
    S.append_entry(ledger, {
        "date": post["date"], "slug": post["slug"], "title": post["title"],
        "monitor_id": post["monitor_id"], "window_id": post["window_id"],
        "dest": post["dest"], "generated_at": post["generated_at"],
    })
    S.save_ledger(ledger)
    print(f"저장: {d.relative_to(D.ROOT)}  (이미지 {len(png)}장)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
