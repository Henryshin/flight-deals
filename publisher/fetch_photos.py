# -*- coding: utf-8 -*-
"""result/<슬러그>/photos.json 에 적힌 위키미디어 커먼즈 사진을 내려받는다.

    python publisher/fetch_photos.py result/2026-09-08-icn-bki-2026-10-05

여행 사진은 **커먼즈에서 라이선스를 확인한 것만** 쓴다. 아무 데서나 가져오면
저작권 문제가 생기고, 네이버는 출처 없는 사진을 저품질 신호로도 본다.
라이선스가 허용 목록에 없으면 받지 않고 그 자리에서 죽는다.

내려받은 것은 post.json 과 나란히 photo-credits.json 에 남는다. CC BY / BY-SA 는
**저작자 표시가 의무**라, post_to_naver.py 가 이 파일을 읽어 글 끝에 출처 문단을
붙인다. 이 파일이 없으면 사진 없이 자리 표시만 들어간다.

photos.json 형식 (사람이 고른 결과를 적어 두는 파일):

    [{"hint": "post.json 의 photo 자리 설명 그대로",
      "commons": "File:....jpg",
      "file": "images/photo-1-....jpg"}]

hint 는 post.json 의 photo 블록과 **문자열이 정확히 같아야** 짝이 맞는다.
"""
import argparse
import html
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://commons.wikimedia.org/w/api.php"
# 위키미디어는 사람이 식별 가능한 User-Agent 를 요구한다.
UA = "FlightDealsBlog/1.0 (travel editorial; Wikimedia attribution)"
# 저작자 표시만으로 쓸 수 있는 것들. 여기 없으면 받지 않는다.
ALLOWED = (
    "CC BY-SA 4.0", "CC BY-SA 3.0", "CC BY-SA 2.0",
    "CC BY 4.0", "CC BY 3.0", "CC BY 2.0",
    "CC0", "Public domain",
)
# 원본은 4~6천 픽셀짜리라 블로그에는 과하다. 비율 유지 축소본을 받는다.
THUMB_WIDTH = 1600


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def strip_html(s):
    # 커먼즈 extmetadata 는 HTML 조각이다. 태그만 벗기면 &amp; 같은 엔티티가
    # 남아서 저작자 표시에 그대로 찍힌다 (2026-09-11 "U.S. Fish &amp; Wildlife").
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def fetch(post_dir):
    spec = json.loads((post_dir / "photos.json").read_text(encoding="utf-8"))
    params = {
        "action": "query", "format": "json",
        "titles": "|".join(x["commons"] for x in spec),
        "prop": "imageinfo", "iiprop": "url|extmetadata|size",
        "iiurlwidth": THUMB_WIDTH,
    }
    raw = json.loads(get(API + "?" + urllib.parse.urlencode(params)))
    pages = {v["title"]: v for v in raw["query"]["pages"].values() if "imageinfo" in v}

    manifest = []
    for item in spec:
        page = pages.get(item["commons"])
        if page is None:
            raise SystemExit("커먼즈에서 못 찾았다: " + item["commons"])
        info = page["imageinfo"][0]
        meta = info["extmetadata"]
        val = lambda k: strip_html(meta.get(k, {}).get("value", ""))
        lic = val("LicenseShortName")
        if lic not in ALLOWED:
            raise SystemExit(
                "허용하지 않는 라이선스라 받지 않는다: "
                + item["commons"] + " -> " + repr(lic)
            )
        manifest.append({
            "hint": item["hint"],
            "file": item["file"],
            "title": item["commons"],
            "url": info.get("thumburl", info["url"]),
            "source_url": info["descriptionurl"],
            "author": val("Artist"),
            "license": lic,
            "license_url": val("LicenseUrl"),
            "date": val("DateTimeOriginal"),
            "description": val("ImageDescription"),
            "modifications": "위키미디어 비례 축소본. 자르기·색 보정 없음",
        })

    for m in manifest:
        dst = post_dir / m["file"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            print("  이미 있음 " + m["file"])
            continue
        dst.write_bytes(get(m["url"]))
        print("  받음 %s (%.0f KB) %s / %s"
              % (m["file"], dst.stat().st_size / 1024, m["author"], m["license"]))

    (post_dir / "photo-credits.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def credit_lines(manifest):
    """글 끝에 붙일 출처 문단. CC BY/BY-SA 의 저작자 표시 의무를 채운다."""
    out = ["사진 출처"]
    for m in manifest:
        out.append(" / ".join(x for x in (
            m["author"], m["title"][5:], m["source_url"],
            m["license"] + (" " + m["license_url"] if m["license_url"] else ""),
            m["modifications"],
        ) if x))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="커먼즈 사진을 내려받는다")
    ap.add_argument("post_dir", help="result/<슬러그> 경로")
    args = ap.parse_args(argv)
    post_dir = Path(args.post_dir).resolve()
    if not (post_dir / "photos.json").exists():
        print("photos.json 이 없다: " + str(post_dir), file=sys.stderr)
        return 1
    manifest = fetch(post_dir)
    print()
    for line in credit_lines(manifest):
        print("  " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
