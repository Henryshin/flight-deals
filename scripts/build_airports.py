#!/usr/bin/env python3
"""docs/airports.js 의 GENERATED_EXT 블록을 전 세계 IATA 공항 데이터로 채운다.

큐레이션된 상단 목록(손으로 관리, 한글 도시/공항명 + 인기순)은 그대로 두고,
그 목록에 없는 나머지 전 세계 정기 IATA 공항을 자동완성 검색에 노출시키기 위한
확장 데이터를 GENERATED_EXT_START / GENERATED_EXT_END 마커 사이에 다시 쓴다.

의존성(오프라인 데이터셋):
    pip install airportsdata pycountry

실행:
    python3 scripts/build_airports.py

멱등(idempotent): 여러 번 돌려도 마커 사이 내용만 교체된다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import airportsdata
import pycountry

ROOT = Path(__file__).resolve().parent.parent
AIRPORTS_JS = ROOT / "docs" / "airports.js"

START = "// ===== GENERATED_EXT_START"
END = "// ===== GENERATED_EXT_END"

# 국가 코드(ISO 3166-1 alpha-2) → 한글 국가명.
# 없는 국가는 pycountry 의 영문명으로 폴백한다.
COUNTRY_KO = {
    "KR": "대한민국", "JP": "일본", "TW": "대만", "HK": "홍콩", "MO": "마카오",
    "CN": "중국", "MN": "몽골", "TH": "태국", "VN": "베트남", "ID": "인도네시아",
    "SG": "싱가포르", "MY": "말레이시아", "PH": "필리핀", "MM": "미얀마",
    "KH": "캄보디아", "LA": "라오스", "BN": "브루나이", "GU": "괌",
    "MP": "북마리아나제도", "PW": "팔라우", "AU": "호주", "NZ": "뉴질랜드",
    "FJ": "피지", "US": "미국", "CA": "캐나다", "MX": "멕시코", "GB": "영국",
    "FR": "프랑스", "DE": "독일", "IT": "이탈리아", "ES": "스페인",
    "PT": "포르투갈", "NL": "네덜란드", "CH": "스위스", "AT": "오스트리아",
    "BE": "벨기에", "CZ": "체코", "HU": "헝가리", "PL": "폴란드", "GR": "그리스",
    "FI": "핀란드", "DK": "덴마크", "SE": "스웨덴", "NO": "노르웨이",
    "IS": "아이슬란드", "IE": "아일랜드", "AE": "아랍에미리트", "QA": "카타르",
    "TR": "튀르키예", "SA": "사우디아라비아", "IL": "이스라엘", "UZ": "우즈베키스탄",
    "KZ": "카자흐스탄", "IN": "인도", "NP": "네팔", "LK": "스리랑카",
    "MV": "몰디브", "BR": "브라질", "AR": "아르헨티나", "CL": "칠레",
    "PE": "페루", "CO": "콜롬비아", "EG": "이집트", "ZA": "남아프리카공화국",
    "KE": "케냐", "ET": "에티오피아", "RU": "러시아", "UA": "우크라이나",
    "RO": "루마니아", "BG": "불가리아", "HR": "크로아티아", "RS": "세르비아",
    "SK": "슬로바키아", "SI": "슬로베니아", "LT": "리투아니아", "LV": "라트비아",
    "EE": "에스토니아", "LU": "룩셈부르크", "MT": "몰타", "CY": "키프로스",
    "AL": "알바니아", "MK": "북마케도니아", "BA": "보스니아헤르체고비나",
    "ME": "몬테네그로", "GE": "조지아", "AM": "아르메니아", "AZ": "아제르바이잔",
    "BD": "방글라데시", "PK": "파키스탄", "LK ": "스리랑카", "BT": "부탄",
    "KG": "키르기스스탄", "TJ": "타지키스탄", "TM": "투르크메니스탄",
    "AF": "아프가니스탄", "IR": "이란", "IQ": "이라크", "JO": "요르단",
    "LB": "레바논", "SY": "시리아", "KW": "쿠웨이트", "BH": "바레인",
    "OM": "오만", "YE": "예멘", "MA": "모로코", "DZ": "알제리", "TN": "튀니지",
    "LY": "리비아", "SD": "수단", "NG": "나이지리아", "GH": "가나",
    "CI": "코트디부아르", "SN": "세네갈", "CM": "카메룬", "TZ": "탄자니아",
    "UG": "우간다", "RW": "르완다", "ZW": "짐바브웨", "ZM": "잠비아",
    "MZ": "모잠비크", "AO": "앙골라", "NA": "나미비아", "BW": "보츠와나",
    "MU": "모리셔스", "SC": "세이셸", "MG": "마다가스카르", "RE": "레위니옹",
    "NC": "누벨칼레도니", "PF": "프랑스령폴리네시아", "WS": "사모아",
    "TO": "통가", "VU": "바누아투", "PG": "파푸아뉴기니", "SB": "솔로몬제도",
    "CK": "쿡제도", "KI": "키리바시", "FM": "미크로네시아", "MH": "마셜제도",
    "NR": "나우루", "TV": "투발루", "PA": "파나마", "CR": "코스타리카",
    "GT": "과테말라", "HN": "온두라스", "SV": "엘살바도르", "NI": "니카라과",
    "BZ": "벨리즈", "CU": "쿠바", "DO": "도미니카공화국", "JM": "자메이카",
    "HT": "아이티", "BS": "바하마", "BB": "바베이도스", "TT": "트리니다드토바고",
    "PR": "푸에르토리코", "AW": "아루바", "CW": "쿠라사오", "KY": "케이맨제도",
    "BM": "버뮤다", "VE": "베네수엘라", "EC": "에콰도르", "BO": "볼리비아",
    "PY": "파라과이", "UY": "우루과이", "GY": "가이아나", "SR": "수리남",
    "BY": "벨라루스", "MD": "몰도바", "GL": "그린란드", "FO": "페로제도",
    "GI": "지브롤터", "AD": "안도라", "MC": "모나코", "SM": "산마리노",
    "LI": "리히텐슈타인", "VA": "바티칸", "DJ": "지부티", "ER": "에리트레아",
    "SO": "소말리아", "SS": "남수단", "CD": "콩고민주공화국", "CG": "콩고",
    "GA": "가봉", "GQ": "적도기니", "TD": "차드", "NE": "니제르", "ML": "말리",
    "BF": "부르키나파소", "MR": "모리타니", "GM": "감비아", "GW": "기니비사우",
    "GN": "기니", "SL": "시에라리온", "LR": "라이베리아", "TG": "토고",
    "BJ": "베냉", "CV": "카보베르데", "ST": "상투메프린시페", "KM": "코모로",
    "MW": "말라위", "LS": "레소토", "SZ": "에스와티니", "BI": "부룬디",
    "CF": "중앙아프리카공화국", "TL": "동티모르",
}


def country_ko(cc: str) -> str:
    if cc in COUNTRY_KO:
        return COUNTRY_KO[cc]
    try:
        c = pycountry.countries.get(alpha_2=cc)
        if c:
            return c.name
    except (KeyError, LookupError):
        pass
    return cc


def city_of(rec: dict) -> str:
    city = (rec.get("city") or "").strip()
    if city:
        return city
    name = (rec.get("name") or "").strip()
    stripped = re.sub(r"\s*(International |Regional |Municipal )?Airport$", "", name).strip()
    if stripped:
        return stripped
    subd = (rec.get("subd") or "").strip()
    return subd or rec["iata"]


def curated_iatas(text: str) -> set[str]:
    """상단 큐레이션 목록(GENERATED_EXT 마커 이전)의 IATA 코드 집합."""
    head = text.split(START, 1)[0]
    return set(re.findall(r"a\('([A-Z0-9]{3})'", head))


def main() -> None:
    text = AIRPORTS_JS.read_text(encoding="utf-8")
    if START not in text or END not in text:
        raise SystemExit("GENERATED_EXT 마커를 찾을 수 없습니다. airports.js 를 확인하세요.")

    curated = curated_iatas(text)
    data = airportsdata.load("IATA")

    rows = []
    for iata in sorted(data):
        if iata in curated:
            continue
        rec = data[iata]
        cc = (rec.get("country") or "").strip().upper()
        if len(cc) != 2:
            continue
        name = (rec.get("name") or "").strip() or f"{iata} Airport"
        rows.append([iata, city_of(rec), name, country_ko(cc), cc])

    body_lines = [
        "    " + json.dumps(r, ensure_ascii=False, separators=(", ", ": ")) + ","
        for r in rows
    ]

    start_idx = text.index(START)
    start_line_end = text.index("\n", start_idx) + 1
    # START 주석 뒤의 안내 주석 2줄 + "var GENERATED_EXT = [" 줄까지 보존
    open_marker = "  var GENERATED_EXT = [\n"
    open_idx = text.index(open_marker, start_line_end)
    close_marker = "  ];\n"
    close_idx = text.index(close_marker, open_idx)

    new_text = (
        text[: open_idx + len(open_marker)]
        + "\n".join(body_lines)
        + ("\n" if body_lines else "")
        + text[close_idx:]
    )

    AIRPORTS_JS.write_text(new_text, encoding="utf-8")
    print(f"GENERATED_EXT: {len(rows)} airports written "
          f"(curated skipped: {len(curated)}, source total: {len(data)})")


if __name__ == "__main__":
    main()
