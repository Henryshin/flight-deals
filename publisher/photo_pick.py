# -*- coding: utf-8 -*-
"""여행 사진 후보를 커먼즈 **품질 등급** 순으로 뽑는다.

'좋은 사진'을 눈대중으로 고르면 매번 결과가 달라진다. 커먼즈에는 커뮤니티가
심사한 등급이 있으므로 그걸 1차 필터로 쓴다 (incategory: 로 검색).

  Featured pictures ★★★   투표로 뽑은 최상위
  Quality images    ★★    기술 품질 심사 통과
  Valued images     ★     그 주제에서 가장 가치 있는 사진

여기에 기계로 확인 가능한 조건을 더한다 — 가로 사진, 최소 2000px, 허용 라이선스.
인물 비중·계절 일치는 여전히 썸네일을 보고 사람이 판단한다.

    python publisher/photo_pick.py "Qingdao coast" "Hong Kong tram"

목적지에 쓸 만한 사진이 아예 적을 수도 있다. 2026-09-09 실측 —
홍콩 340건 · 타이페이 302건인데 **칭다오는 Quality images 가 3건뿐**이었다.
후보를 고를 때 이 숫자를 먼저 세어 보면 "사진이 없어서 글이 약해지는" 걸 피할 수 있다.
"""
import json, re, sys, time, urllib.parse, urllib.request

API = "https://commons.wikimedia.org/w/api.php"
UA = "FlightDealsBlog/1.0 (travel editorial; Wikimedia attribution)"
ALLOWED = ("CC BY-SA 4.0","CC BY-SA 3.0","CC BY-SA 2.0",
           "CC BY 4.0","CC BY 3.0","CC BY 2.0","CC0","Public domain")
TIERS = [("Featured pictures","★★★"), ("Quality images","★★"), ("Valued images","★")]
MIN_WIDTH = 2000

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())

def strip(s): return re.sub(r"<[^>]+>", "", s or "").strip()

def _query(search, limit):
    q = urllib.parse.urlencode({
        "action":"query","format":"json","generator":"search",
        "gsrsearch":f"filetype:bitmap {search}","gsrnamespace":"6",
        "gsrlimit":str(limit),"prop":"imageinfo",
        "iiprop":"url|extmetadata|size","iiurlwidth":"420"})
    out=[]
    for p in (get(f"{API}?{q}").get("query",{}).get("pages") or {}).values():
        ii=(p.get("imageinfo") or [{}])[0]; md=ii.get("extmetadata") or {}
        lic=strip((md.get("LicenseShortName") or {}).get("value"))
        w,h=ii.get("width",0),ii.get("height",0)
        if lic not in ALLOWED or w < MIN_WIDTH or w < h: continue
        out.append({"title":p["title"],"lic":lic,"wh":f"{w}x{h}",
                    "thumb":ii.get("thumburl"),
                    "author":strip((md.get("Artist") or {}).get("value"))[:26]})
    out.sort(key=lambda r:-int(r["wh"].split("x")[0]))
    return out

def pick(term, want=8):
    seen, res = set(), []
    for cat, mark in TIERS:
        for r in _query(f'{term} incategory:"{cat}"', 20):
            if r["title"] in seen: continue
            seen.add(r["title"]); r["tier"]=mark; res.append(r)
    if len(res) < want:                      # 등급 있는 게 부족하면 일반 검색으로 채운다
        for r in _query(term, 25):
            if r["title"] in seen: continue
            seen.add(r["title"]); r["tier"]=""; res.append(r)
    return res[:want*2]

def places(names):
    """장소를 **커먼즈 사진 장수** 순으로 줄 세운다.

    어느 장소를 글에 넣을지 정하는 데 쓴다. 사람이 많이 가고 찍을 만한 곳일수록
    사진이 쌓이므로 인기의 대리지표가 되고, 동시에 내가 고를 재고와 같은 값이다.

    다른 후보도 재 봤지만 우리 목적엔 안 맞았다 (2026-09-09 실측):
      - 위키 사용 횟수: 딤섬 206곳 vs 좋은 풍경 사진 0곳. 사진의 매력이 아니라
        '그 사진이 들어갈 문서가 있나'를 잰다
      - 위키백과 조회수: 지우펀 143회. 백과사전 조회는 여행 욕구와 별개다

    **반드시 정확구로 검색한다.** 따옴표 없이 Victoria Peak 을 넣으면 두 단어가
    따로 매칭돼 28만 건이 나온다.
    """
    out=[]
    for n in names:
        q=urllib.parse.urlencode({"action":"query","format":"json","list":"search",
            "srsearch":f'filetype:bitmap "{n}"',"srnamespace":"6","srlimit":"1"})
        try:
            tot=get(f"{API}?{q}")["query"]["searchinfo"]["totalhits"]
        except Exception:
            tot=None
        out.append((n,tot)); time.sleep(2.0)   # 커먼즈가 429 를 잘 낸다
    out.sort(key=lambda r: -(r[1] or -1))
    return out


if sys.argv[1:2] == ["--places"]:
    print(f"{'장소':30}{'커먼즈 사진':>10}")
    print('-'*42)
    for n, c in places(sys.argv[2:]):
        print(f"{n:30}{str(c):>10}")
    sys.exit(0)

for term in sys.argv[1:]:
    res = pick(term)
    n = sum(1 for r in res if r["tier"])
    print(f"\n### {term}   (등급 있는 것 {n}건)")
    for r in res[:9]:
        print(f"  {r['tier'] or '  -':4} {r['wh']:>11}  {r['lic']:<14} {r['title'][5:58]}")
