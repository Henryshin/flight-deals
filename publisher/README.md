# 네이버 발행 — 이 저장소에는 자동화 코드가 없습니다

발행은 **이미 검증된 별도 도구**가 담당합니다. 여기서 다시 만들지 마세요.

```
C:\Users\klplm\Desktop\유현\01.개인\0.사다리 올라가기\01.주식리서치 보고서\
  98.네이버블로그자동화_v1.0\README.md      ← 정본
```

그 README에 login.py · post.py · lib.py 사용법과 삽질 기록이 전부 있습니다.
**헤매고 있다면 십중팔구 처음부터 다시 만들고 있는 것입니다.**

## 왜 여기 코드가 없는가

한때 `naver_draft.py` 가 있었지만 지웠습니다. 위 도구가 실물 SmartEditor 로
검증한 내용과 여러 군데 어긋났기 때문입니다 — 클립보드 붙여넣기(자동 번호매기기
폭탄), `Control+End`(문단 끝이 아니라 화면 줄 끝), 삽입 후 검증 없음(조용한 실패).
검증된 도구가 있는데 비슷한 걸 하나 더 두면 둘 다 썩습니다.

## flight-deals 가 내놓는 것

`scripts/blog_save.py` 가 글 하나를 이렇게 남깁니다.

```
posts/<날짜>-<노선>-<연휴>/
  post.json      제목 · 태그 · 블록 배열 (정본)
  body.html      미리보기 / 수동 복붙용
  body.txt       평문
  images/card-price.png, card-trend.png
```

`post.json` 의 `blocks` 는 순서열입니다.

```json
{"type": "html",  "content": "<p>...</p>"}
{"type": "card",  "card": "price", "file": "images/card-price.png"}
{"type": "photo", "hint": "코타키나발루 탄중아루 해변 석양"}
```

- `html` — SmartEditor 가 보존하는 태그만 씁니다
  (`p br h3 strong em ul ol li table thead tbody tr th td a blockquote`)
- `card` — 자동 생성된 가격 카드 PNG. 절대경로로 업로드하면 됩니다
- `photo` — **사용자가 직접 넣을 사진 자리.** 여행 사진은 저작권 때문에
  자동으로 못 넣습니다. 초안에 자리 표시만 남기고, 발행 전에 사람이 채웁니다

## 연결 방법

`post_to_naver.py` 가 `post.json` 을 읽어 에디터에 직접 채웁니다. CDP 로 사용자가
띄워 둔 크롬에 붙는 방식이라 **크롬이 떠 있는 그 PC 에서** 돌려야 합니다.

```bat
:: 1. 크롬을 원격 디버깅 포트로 띄우고 직접 로그인
::    (PowerShell 5.1 은 && 안 됨, ; 를 쓸 것)
cd "...\98.네이버블로그자동화_v1.0"; python login.py

:: 2. 네이버 블로그 글쓰기 화면을 연 상태에서
cd C:\side_PJT\Blog_flight\flight-deals
python publisher\post_to_naver.py posts\2026-09-08-icn-bki-2026-10-05
```

> ⚠️ **실물 SmartEditor 로는 아직 한 번도 돌려보지 않았습니다.** 원격 컨테이너에서
> 네이버가 막혀 있어 가짜 에디터로 배선만 확인했습니다. 첫 실행은 로그를 보면서
> 하시고, 멈추는 지점을 알려주시면 고칩니다. 셀렉터는 `post_to_naver.py` 상단
> 상수 몇 개에 모여 있습니다.

작성 방식(문단 삽입·표 처리)은 이 저장소 것이고, **구동 방식(CDP·insert_text·
커서 처리)만** 검증된 도구에서 가져왔습니다.

## 지키는 것

- **발행은 자동화하지 않습니다.** 제목·본문·태그까지만 채우고, 예약 시각과
  최종 발행 클릭은 사람 몫입니다. ("발행" 버튼은 설정 패널을 여는 것일 뿐이고
  진짜 발행 버튼은 그 안에 따로 있습니다.)
- `photo` 자리를 채우고 한 번 읽어본 뒤 발행합니다. 기계적으로 찍어낸 티가
  나는 글은 네이버가 저품질로 분류합니다.
