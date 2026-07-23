# 항공권 특가 대시보드 (GitHub 자동 운영)

연휴를 낀 항공권 특가를 GitHub Actions가 4시간마다 자동 수집하고, GitHub Pages로 대시보드를 공개합니다.
로컬 PC를 켜둘 필요 없이 GitHub에서 알아서 돌아갑니다.

## 구조

```
data/routes.json      # 관심 노선 등록 (여기에 추가/삭제)
data/prices.csv        # 가격 이력 (Actions가 자동으로 append & 커밋, merge=union)
data/collect_status.json  # 노선별 수집 상태/실패 사유 (collect.py가 기록)
scripts/collect.py      # 크롤링 실행 → prices.csv에 append (연휴 + 평시 기준가)
scripts/build_dashboard_data.py  # prices.csv → docs/data/*.json 생성 (할증률 matrix 포함)
docs/index.html        # GitHub Pages가 서빙하는 대시보드
.github/workflows/collect.yml   # 4시간마다 자동 수집하는 cron (data/ 만 커밋)
.github/workflows/build.yml     # data/** push 시 docs/data/*.json 재생성·커밋
tests/test_smoke.py    # 브라우저 없이 도는 스모크 테스트 (python tests/test_smoke.py)
```

수집과 빌드가 분리되어 있어서:
- 노선 등록/삭제는 다음 수집을 기다리지 않고 1분 내 대시보드에 반영됩니다.
- 수십 분짜리 수집 런이 생성 파일(docs/data) 때문에 rebase 충돌로 죽는 일이 없습니다
  (prices.csv 는 `.gitattributes` 의 `merge=union` 으로 동시 런과도 자동 병합).

### 연휴 가성비 (할증률)

수집기는 연휴 날짜 외에 노선당 하루 1회 **평시(비연휴) 기준가**도 수집합니다.
`build_dashboard_data.py` 가 연휴 윈도우별로 `할증률 = 연휴 최저가 / 평시 기준가 중앙값` 을
계산해 `docs/data/matrix.json` 으로 내보내고, 대시보드의 "연휴 가성비 보드"가
연휴를 골라 할증률 낮은 순으로 여행지를 랭킹합니다. 기준가 표본이 3개 이상 쌓여야
정식(티어 A) 지표로 표시되며, 그 전에는 "기준가 수집 중"으로 나옵니다.

## 노선 추가/삭제

`data/routes.json`을 수정해서 커밋 & 푸시하면 다음 수집 주기(최대 4시간 내)부터 반영됩니다.

```json
{"origin": "ICN", "destination": "BKK", "label": "인천->방콕"}
```

- `origin`/`destination`은 IATA 공항 코드
- 구글 플라이트 크롤러는 도시명을 인식하므로, 새 공항 코드를 쓰려면 `collector/google_flights_crawler.py`의 `AIRPORT_CITY` 표에도 추가해야 함 (단, 대시보드에서 등록하면 도시명이 `routes.json`에 함께 저장되어 폴백 표가 없어도 동작)

## 공항 검색 데이터베이스 (`docs/airports.js`)

대시보드의 항목 등록 자동완성은 `docs/airports.js`의 공항 목록을 사용합니다. 목록은 두 부분으로 구성됩니다.

- **상단 큐레이션 목록**: 한국인 여행자 기준 인기순으로 한글 도시/공항명을 손으로 관리 (검색 우선순위 상단)
- **`GENERATED_EXT` 블록**: 전 세계 정기 IATA 공항 전체를 자동 생성 (시모지시마 등 소규모·지방 공항 포함)

`GENERATED_EXT` 블록은 직접 수정하지 말고 생성 스크립트로 갱신합니다.

```bash
pip install airportsdata pycountry
python scripts/build_airports.py
```

큐레이션 목록에 새 공항을 예쁜 한글명으로 추가하고 싶으면 `airports.js` 상단(마커 위)에 `a(...)` 한 줄을 넣으면 됩니다. 같은 IATA가 큐레이션에 있으면 생성 스크립트가 자동으로 중복을 건너뜁니다.

## 로컬 테스트

```powershell
pip install -r requirements.txt
playwright install chromium
python scripts/collect.py
python scripts/build_dashboard_data.py
```

`docs/index.html`을 브라우저로 직접 열거나 `python -m http.server` 로 로컬 확인 가능.

## GitHub Pages 설정

저장소 Settings → Pages → Source를 `main` 브랜치의 `/docs` 폴더로 지정.

## 주의사항

- 구글 플라이트 화면 크롤링이므로 사이트 구조가 바뀌면 `collector/google_flights_crawler.py`의 파싱 로직을 갱신해야 함
- GitHub Actions 무료 크레딧은 public 저장소 기준 무제한이지만, 실제 실행 간격은 GitHub의 스케줄 지연으로 정확히 4시간이 아닐 수 있음
- 가격 이력은 `data/prices.csv`에 계속 쌓이므로, 데이터가 많아지면 저장소 용량/커밋 히스토리가 늘어남 (주기적으로 오래된 데이터 정리 고려)
