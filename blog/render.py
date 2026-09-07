"""초안 마크다운 -> 네이버 SmartEditor 에 붙여넣을 블록.

세션에서 쓴 글(draft.md)을 post.json 의 blocks 배열로 바꾼다.
SmartEditor 는 이미지를 클립보드로 받지 못하므로, 본문을
「텍스트 조각 -> 이미지 -> 텍스트 조각」 순서열로 표현해야 한다.

draft.md 문법 (일부러 작게 유지)
--------------------------------
    ---
    title: 미야자키 항공권, 아시아나 직항 18만원대부터
    category: 일본
    tags: [미야자키항공권, 미야자키여행]
    monitor_id: ICN-KMI
    window_id: 2026-09-24
    ---

    ## 소제목                 -> h3
    **볼드**  [링크](url)      -> 인라인
    - 불릿                     -> ul
    > 인용                     -> blockquote (항공편 정보 박스)
    | a | b |                  -> table
    [[CARD:price]]             -> 자동 생성 가격 카드 PNG
    [[PHOTO: 아오시마 해안]]    -> 사용자가 직접 넣을 사진 자리

빈 줄이 문단 구분, 문단 안의 줄바꿈은 <br> 로 살린다 (예시 포스팅이 2~3줄씩
끊어 쓰는 문체라 줄바꿈 자체가 스타일이다).
"""
import html
import re

# SmartEditor 붙여넣기에서 살아남는 태그만 쓴다. style/class 는 어차피 제거된다.
ALLOWED_TAGS = {
    "p", "br", "h3", "strong", "em", "ul", "ol", "li",
    "table", "thead", "tbody", "tr", "th", "td", "a", "blockquote",
}

CARD_RE = re.compile(r"^\[\[CARD:\s*([a-z_]+)\s*\]\]$")
PHOTO_RE = re.compile(r"^\[\[PHOTO:\s*(.+?)\s*\]\]$")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
TABLE_SEP_RE = re.compile(r"^\|[\s:|-]+\|$")


class DraftError(ValueError):
    """초안 형식 오류."""


def parse_front_matter(text):
    """'---' 로 둘러싼 머리말과 본문을 분리."""
    if not text.startswith("---"):
        raise DraftError("초안은 '---' 머리말로 시작해야 한다")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise DraftError("머리말을 닫는 '---' 가 없다")
    meta = {}
    for line in parts[1].strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if val.startswith("[") and val.endswith("]"):
            meta[key] = [v.strip() for v in val[1:-1].split(",") if v.strip()]
        else:
            meta[key] = val
    return meta, parts[2].strip()


def _inline(text):
    """인라인 마크업만 HTML 로. 나머지는 전부 이스케이프한다."""
    out, pos = [], 0
    for m in LINK_RE.finditer(text):
        out.append(_bold(text[pos:m.start()]))
        out.append(f'<a href="{html.escape(m.group(2), quote=True)}">'
                   f"{_bold(m.group(1))}</a>")
        pos = m.end()
    out.append(_bold(text[pos:]))
    return "".join(out)


def _bold(text):
    out, pos = [], 0
    for m in BOLD_RE.finditer(text):
        out.append(html.escape(text[pos:m.start()]))
        out.append(f"<strong>{html.escape(m.group(1))}</strong>")
        pos = m.end()
    out.append(html.escape(text[pos:]))
    return "".join(out)


def _flush(buf, blocks):
    if buf:
        blocks.append({"type": "html", "content": "".join(buf)})
        buf.clear()


def parse_body(body):
    """본문 텍스트 -> 블록 배열."""
    blocks, buf = [], []
    lines = body.replace("\r\n", "\n").split("\n")
    i, n = 0, len(lines)

    while i < n:
        raw = lines[i]
        line = raw.strip()

        if not line:
            i += 1
            continue

        m = CARD_RE.match(line)
        if m:
            _flush(buf, blocks)
            blocks.append({"type": "card", "card": m.group(1)})
            i += 1
            continue

        m = PHOTO_RE.match(line)
        if m:
            _flush(buf, blocks)
            blocks.append({"type": "photo", "hint": m.group(1)})
            i += 1
            continue

        if line.startswith("## "):
            buf.append(f"<h3>{_inline(line[3:].strip())}</h3>")
            i += 1
            continue

        if line.startswith("- "):
            items = []
            while i < n and lines[i].strip().startswith("- "):
                items.append(f"<li>{_inline(lines[i].strip()[2:].strip())}</li>")
                i += 1
            buf.append("<ul>" + "".join(items) + "</ul>")
            continue

        if line.startswith("> "):
            quoted = []
            while i < n and lines[i].strip().startswith("> "):
                quoted.append(_inline(lines[i].strip()[2:].strip()))
                i += 1
            buf.append("<blockquote>" + "<br>".join(quoted) + "</blockquote>")
            continue

        if line.startswith("|") and line.endswith("|"):
            rows = []
            while i < n:
                cur = lines[i].strip()
                if not (cur.startswith("|") and cur.endswith("|")):
                    break
                i += 1
                if TABLE_SEP_RE.match(cur):
                    continue
                rows.append([c.strip() for c in cur[1:-1].split("|")])
            if rows:
                head = "".join(f"<th>{_inline(c)}</th>" for c in rows[0])
                body_rows = "".join(
                    "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>"
                    for r in rows[1:]
                )
                buf.append(
                    f"<table><thead><tr>{head}</tr></thead>"
                    f"<tbody>{body_rows}</tbody></table>"
                )
            continue

        # 일반 문단: 빈 줄이 나올 때까지 모아서 <br> 로 잇는다
        para = []
        while i < n and lines[i].strip():
            cur = lines[i].strip()
            if (cur.startswith(("## ", "- ", "> ", "|"))
                    or CARD_RE.match(cur) or PHOTO_RE.match(cur)):
                break
            para.append(_inline(cur))
            i += 1
        if para:
            buf.append("<p>" + "<br>".join(para) + "</p>")

    _flush(buf, blocks)
    return blocks


def blocks_to_html(blocks, image_files=None):
    """미리보기/수동 복붙용 전체 HTML 조각."""
    files = image_files or {}
    out = []
    for b in blocks:
        if b["type"] == "html":
            out.append(b["content"])
        elif b["type"] == "card":
            src = files.get(b["card"], f"images/card-{b['card']}.png")
            out.append(f'<p><img src="{html.escape(src, quote=True)}" alt="가격 카드"></p>')
        elif b["type"] == "photo":
            out.append(f"<p><em>[사진 자리] {html.escape(b['hint'])}</em></p>")
    return "\n".join(out)


def blocks_to_text(blocks):
    """평문 폴백. 클립보드 HTML 붙여넣기가 실패했을 때 쓴다."""
    out = []
    for b in blocks:
        if b["type"] == "html":
            t = re.sub(r"<br\s*/?>", "\n", b["content"])
            t = re.sub(r"</(p|h3|li|tr|blockquote)>", "\n", t)
            t = re.sub(r"<[^>]+>", "", t)
            out.append(html.unescape(t).strip())
        elif b["type"] == "card":
            out.append(f"[가격 카드: {b['card']}]")
        elif b["type"] == "photo":
            out.append(f"[사진 자리: {b['hint']}]")
    return "\n\n".join(x for x in out if x)


def used_tags(blocks):
    """블록에 실제로 등장한 태그 이름 (허용목록 테스트용)."""
    tags = set()
    for b in blocks:
        if b["type"] == "html":
            tags.update(m.lower() for m in re.findall(r"</?([a-zA-Z0-9]+)", b["content"]))
    return tags
