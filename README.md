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

### 두 개의 보드 (같은 matrix.json, 축만 반대)

`matrix.json` 은 `cells[연휴id][모니터id]` 형태의 2차원 표라, 어느 축으로도 읽을 수 있습니다.

| 보드 | 질문 | 읽는 방향 |
| --- | --- | --- |
| **연휴 특가 보드** | "이 연휴엔 어디가 싼가" | 연휴 고정 → 여행지 랭킹 |
| **여행지별 연휴 보드** | "이 여행지는 어느 연휴에 가야 싼가" | 여행지 고정 → 연휴 나열 |

여행지별 연휴 보드는 여행지 행을 클릭하면 그 아래로 연휴별 최저가가 **싼 순서대로**
펼쳐지고, 오른쪽 막대로 연휴 간 가격 차이를 비교할 수 있습니다. 노선 관리(수집 상태,
목표가, 박수/경유 수정 ✎, 삭제 ✕)도 이 보드에 그대로 들어 있습니다 — 모니터가 둘 이상인
노선(예: 직항 전용 + 경유 허용)은 펼친 패널 안에서 모니터별로 다룹니다.
두 보드 모두 프론트엔드에서 같은 `matrix.json` 을 읽으므로 수집·빌드 파이프라인은 동일합니다.

### 휴일 가치 (비용-편익 추천)

최저가만 좇으면 '목금토일 연휴에 화~월'처럼 연차 효율이 나쁜 일정이 추천되는 문제가
있어, 일정마다 **연차 소모 수**와 **덤 휴일**(여행 기간 안에서 연차 없이 얻는 날 수 —
주말·공휴일)을 계산합니다. 덤 휴일은 반드시 여행 기간 **안**에서만 셉니다. 출발 전/귀국
후에 집에서 보내는 인접 주말은 그 여행과 무관하므로 세지 않습니다.

- 수집기(`collect.py`)는 연휴 구간 안 후보 외에, 출발/귀국을 인접 주말 경계까지 밀어
  같은 연차로 여행 기간 안의 덤 휴일이 더 많은 **앵커 후보**도 크롤합니다 (성탄절~신정
  브릿지 포함). 후보 총수가 상한을 넘으므로 날짜 기반 회전으로 며칠에 걸쳐 전부 순환
  수집됩니다.
- 빌드(`build_dashboard_data.py`)는 셀마다 (가격↑, 덤휴일↑) **파레토 프런티어** 일정
  목록(`pairs`, 최대 6개)을 내보냅니다. 덤 휴일 = 여행일 − 연차.
- 대시보드 필터의 **휴일 가치**(하루 = n만원, 기본 10)를 설정하면
  `실질 비용 = 가격 − 가치 × 덤휴일` 이 최소인 일정을 추천합니다. 0이면 항상 최저가.
  재빌드 없이 브라우저에서 즉시 재계산되며 값은 브라우저에 저장됩니다.
- 휴일 가치는 **어느 연휴를 갈지**에도 적용됩니다. 두 보드의 대표 연휴를 실질비용이
  가장 낮은 연휴로 고르고(특가 보드는 티어를 먼저 보아 '수집 중' 칸이 대표가 되는 것은
  막습니다), 여행지별 보드 펼침 패널에서 `실질 최적`(파란 배지)과 `최저`(초록 배지)를
  따로 표시합니다. 가치가 0이면 예전과 같이 '가장 싼 연휴'가 대표입니다.

**연차 상한** (기본 5개, 비우면 무제한)

실질 비용 공식은 덤 휴일만 편익으로 보고 **연차 소모에는 페널티가 없습니다.** 덤 휴일은
여행이 길어지면 기계적으로 늘어나므로(주말 하나 낄 때마다 +2), 한도가 없으면 '연차 9개
쓰는 14박'처럼 연차를 태우는 일정이 추천으로 올라옵니다. 연차 상한은 그 예산 제약입니다.

- 상한을 넘는 일정은 추천 후보에서 제외합니다 (`eligiblePairs`). 최저가 열은
  `cell.min_price`(= `pairs[0]`)와 묶여 있어 그대로 두고, 추천만 상한 안에서 고릅니다.
- 상한 안에 후보가 하나도 없으면 `⚠ 연차 N개 필요 (상한 M개 초과)` 로 알립니다 —
  갈 수 없는 일정을 추천처럼 두지 않기 위함입니다.
- 상한이 낮을수록 후보가 급격히 줄어듭니다(실측: 상한 5 → 561/562 셀에 후보 있음,
  상한 4 → 453/562, 상한 3 → 251/562). 장거리 노선은 최소 박수 때문에 연차 4~5개가
  기본이라, 기본값을 5로 두고 필요하면 사용자가 조입니다.

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

## 텔레그램 특가 알림

`docs/data/deals.json`(대시보드의 "변동 항목" — 같은 연휴 윈도우·박수 기준 최근 평균가 대비
15% 이상 하락한 특가)이 새로 생기거나 더 싸질 때마다 텔레그램으로 알려줍니다.
`build.yml`이 대시보드 데이터를 재생성한 직후 `scripts/notify_telegram.py`를 실행합니다.

### 설정 (약 5분)

1. 텔레그램에서 [@BotFather](https://t.me/BotFather)에게 `/newbot`을 보내 봇을 만들고
   토큰(`123456:ABC-...` 형태)을 받습니다.
2. 알림 받을 대화의 chat id를 확인합니다: 만든 봇과 먼저 아무 대화나 나눈 뒤,
   `https://api.telegram.org/bot<토큰>/getUpdates`를 열어 `"chat":{"id":...}` 값을 확인
   (그룹방에 추가했다면 음수 id가 나올 수 있습니다).
3. 저장소 Settings → Secrets and variables → Actions → **New repository secret**으로 추가:
   | 이름 | 값 |
   |------|-----|
   | `TELEGRAM_BOT_TOKEN` | 1번에서 받은 토큰 |
   | `TELEGRAM_CHAT_ID` | 2번에서 확인한 chat id |

두 시크릿을 설정하지 않으면 알림 스텝은 그냥 건너뛰고 나머지 파이프라인은 평소대로 동작합니다.

### 동작 방식

- 알림 기준(할인율)은 기본적으로 `deals.json`에 뜨는 모든 항목(15%↑ 하락)이며,
  더 엄격하게 쓰려면 `notify_telegram.py`를 실행하는 워크플로 스텝에
  `TELEGRAM_MIN_DISCOUNT_PCT` 환경변수(%)를 추가하면 됩니다.
- 같은 노선·날짜·직항/경유 조합은 한 번 알린 뒤 할인율이 5%p 이상 더 떨어지지 않으면
  다시 알리지 않습니다 (`data/telegram_notified.json`에 기록). 특가 조건을 벗어나면
  기록이 지워져서, 나중에 다시 떨어지면 새 특가로 재알림됩니다.
- 로컬에서 테스트하려면 `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`를 환경변수로 설정하고
  `python scripts/build_dashboard_data.py && python scripts/notify_telegram.py`를 실행하세요.

## 주의사항

- 구글 플라이트 화면 크롤링이므로 사이트 구조가 바뀌면 `collector/google_flights_crawler.py`의 파싱 로직을 갱신해야 함
- GitHub Actions 무료 크레딧은 public 저장소 기준 무제한이지만, 실제 실행 간격은 GitHub의 스케줄 지연으로 정확히 4시간이 아닐 수 있음
- 가격 이력은 `data/prices.csv`에 쌓이지만, 매 수집 직후 `scripts/prune_prices.py`가 계산에 안 쓰이는 90일 초과분(`PRUNE_RETENTION_DAYS`)을 자동으로 잘라내 무한정 커지지 않음
