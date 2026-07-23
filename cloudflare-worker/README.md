# 등록 프록시 설정 (Cloudflare Worker)

방문자가 GitHub 토큰 없이 노선을 등록/삭제할 수 있게 해주는 무료 서버(Worker)입니다.
토큰은 이 Worker 안에만 비밀로 보관되고, 페이지는 이 Worker를 통해서만 GitHub에 씁니다.

## 준비물
- 무료 Cloudflare 계정 (https://dash.cloudflare.com/sign-up)
- GitHub Fine-grained 토큰 1개 — Repository: `Henryshin/flight-deals`,
  권한: **Contents: Read and write**, **Actions: Read and write**
- 친구들에게 알려줄 **공유 암호**(아무 문자열) — 스팸 방지용 (원치 않으면 생략 가능)

## 단계 (약 10분, 코딩 없음)

1. Cloudflare 대시보드 → **Workers & Pages** → **Create** → **Create Worker**.
   이름은 예: `flight-register` → **Deploy**.

2. **Edit code** 클릭 → 기본 코드를 전부 지우고, 이 폴더의 `worker.js` 내용을
   **통째로 붙여넣기** → **Deploy**.

3. Worker의 **Settings → Variables and Secrets** 에서 아래를 추가:
   | 이름 | 종류 | 값 |
   |------|------|-----|
   | `GH_TOKEN` | Secret | 위에서 만든 GitHub 토큰 |
   | `SHARE_PASS` | Secret | 공유 암호 (생략 시 암호 없이 누구나 등록) |
   | `ALLOW_ORIGIN` | Text | `https://henryshin.github.io` |
   추가 후 **Deploy** 한 번 더.

4. 배포된 Worker 주소를 복사합니다. 형태: `https://flight-register.<계정>.workers.dev`

5. 그 주소를 알려주시면, 제가 `docs/index.html`의 `PROXY_URL`에 넣어 연결합니다.
   그 순간부터 방문자는 토큰 입력창 대신 **공유 암호만**(설정했다면) 입력하면 바로 등록됩니다.

## 동작
- 등록/삭제/수집이 이 Worker를 거쳐 실행됩니다.
- 서버에서 공항 코드·중복·id를 다시 검증하므로 클라이언트 조작에 안전합니다.
- 동시 등록으로 인한 저장 충돌은 자동 재시도로 처리합니다.
- 등록 즉시 해당 노선만 1회 수집을 트리거합니다.

## 주의
- 공용 `routes.json` 하나를 모두가 공유합니다 → 소규모(친구·소모임)용입니다.
  대규모 공개에는 맞지 않습니다(수집이 구글 화면 크롤링이라 노선이 많아지면 한계).
- 토큰이 노출되면(예: 실수로 커밋) 즉시 GitHub에서 폐기하고 새로 발급하세요.
