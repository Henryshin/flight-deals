# 네이버 임시저장 발행기

`posts/` 의 최신 글을 네이버 블로그 **임시저장**까지 올린다.
**발행은 하지 않는다.** 확인하고 사진을 얹은 뒤 발행 버튼은 사람이 누른다.

## 왜 PC 에서 도는가

네이버는 해외·데이터센터 IP 로그인을 기기 인증으로 막는다. GitHub Actions
러너(미국)에서는 로그인 자체가 거의 확실히 실패한다. 그래서 글 생성은
클라우드에서, 네이버 접속은 국내 가정용 IP 인 PC 에서 한다.

## 최초 1회 세팅

```bat
cd C:\side_PJT\Blog_flight\flight-deals

pip install playwright
playwright install chromium

copy publisher\config.example.json publisher\config.json
:: config.json 을 열어 blog_id 를 본인 블로그 아이디로 바꾼다

python publisher\naver_draft.py --login
```

`--login` 은 브라우저를 띄우고 네이버 로그인 페이지로 간다. **직접 로그인**하고
(2단계 인증 포함) 콘솔에서 Enter 를 누르면 세션이 `publisher/profile/` 에 남는다.
이후로는 재로그인이 없다.

### 네이버에 붙기 전에 — 자체 점검

네이버를 건드리지 않고 발행기 메커니즘만 먼저 확인할 수 있다.

```bat
start /b python -m http.server 8899 --directory publisher
python publisher\test_against_mock.py
```

가짜 SmartEditor(`mock_editor.html`)를 띄우고 `naver_draft.py` 의 실제 함수를
그대로 돌린다. 통과하면 클립보드 서식 붙여넣기·이미지 업로드·블록 순서·
**발행 버튼 차단**이 정상이라는 뜻이다.

여기서 통과했는데 실제 네이버에서 실패하면 원인은 거의 확실히 **셀렉터**다
(로직이 아니라). 아래 "안 될 때" 로 간다.

> 아이디·비밀번호는 이 스크립트에도, 저장소에도, GitHub Secrets 에도 저장되지
> 않는다. 유일한 자격증명은 PC 에 남는 브라우저 프로필이고, 이건 `.gitignore`
> 되어 있다. `git status` 에 `publisher/profile/` 이 보이면 **절대 커밋하지 말 것.**

## 매일

작업 스케줄러에 `publisher\run_draft.bat` 을 등록한다.

| 설정 | 값 |
|---|---|
| 트리거 | 매일 08:30 (브리핑 워크플로가 07:00 KST 에 도니 여유 있게) |
| 보안 옵션 | **사용자가 로그온한 경우에만 실행** ← 헤드풀 브라우저라 필수 |
| 시작 위치 | `C:\side_PJT\Blog_flight\flight-deals` |
| 조건 | "컴퓨터의 AC 전원이 켜져 있는 경우에만" **해제** |
| 설정 | "예약 시간을 놓친 경우 가능한 한 빨리 시작" 체크 |
| 설정 | "작업이 이미 실행 중이면 새 인스턴스를 시작하지 않음" |

`run_draft.bat` 안의 `cd /d` 경로를 실제 클론 경로로 맞추는 것을 잊지 말 것.

## 명령

```bat
python publisher\naver_draft.py --dry-run     :: 네이버 접속 없이 대상 글만 확인
python publisher\naver_draft.py               :: 실제 임시저장
python publisher\naver_draft.py --keep-open   :: 끝나도 브라우저를 닫지 않는다
python publisher\naver_draft.py --force       :: 이미 올린 글을 다시 올린다
python publisher\naver_draft.py --no-pull     :: git pull 생략
```

## 발행 전에 할 일

임시저장된 글에는 `[여기에 사진: ...]` 자리가 들어 있다.

1. 그 자리에 본인 사진을 넣고 표시 문구는 지운다
2. 태그를 붙인다 (콘솔에 출력된 목록을 복사해 쓰면 된다)
3. 한 번 읽어보고 어색한 문장을 손본다
4. 발행

> 3번이 중요하다. 네이버는 기계적으로 찍어낸 티가 나는 글을 저품질로 분류한다.
> 사진을 넣는 김에 한두 문장 손보는 습관이 가장 확실한 방어다.

## 태그는 왜 자동으로 안 넣나

SmartEditor 에서 태그 입력란은 본문이 아니라 **발행 사이드 패널** 안에 있다.
태그를 넣으려고 그 패널을 열면 발행 버튼이 클릭 한 번 거리에 놓인다.
사고로 발행되는 것보다 태그를 손으로 붙이는 편이 낫다고 판단했다.

## 안 될 때

실패하면 `publisher/diagnostics/<시각>/` 에 스크린샷 · HTML · 스택트레이스가
남는다. 로그는 `publisher/logs/draft.log`.

가장 흔한 원인은 **네이버가 에디터 DOM 을 바꾼 것**이다. 이 경우 로그에
"후보 셀렉터를 하나도 못 찾았다" 가 찍힌다. 고치는 법:

```bat
python publisher\naver_draft.py --keep-open
```

로 띄워 놓고 개발자도구(F12)에서 실제 요소를 찾은 뒤,
**`publisher/naver_selectors.py` 한 파일만** 고친다. 후보 리스트 맨 앞에 새 셀렉터를
추가하면 된다.

`SAVE` 목록에는 **절대 '발행' 이 들어간 셀렉터를 넣지 말 것.**
`assert_not_publish()` 가 막긴 하지만, 애초에 넣지 않는 게 맞다.

로그인이 풀렸으면 exit code 2 와 함께 "로그인이 풀렸거나 DOM 이 바뀌었다" 가
찍힌다. `--login` 을 다시 실행한다.

## 커밋하지 않는 것

`.gitignore` 에 이미 들어 있다.

```
publisher/profile/        ← 로그인 세션. 사실상 자격증명
publisher/state.json      ← 어디까지 올렸는지
publisher/diagnostics/    ← 실패 스크린샷
publisher/logs/
publisher/config.json
```
