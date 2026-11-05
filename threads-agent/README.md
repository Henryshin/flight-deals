# threads-agent (homepc 전용)

텔레그램에서 고른 Threads 글을 **Threads 웹에 임시저장**하고, 사용자가 고쳐서 게시한 형식을
Worker(`threads:style`)에 돌려줘서 다음 글에 반영한다. Threads API 에는 임시저장이 없어서 웹을 쓴다.

실제 실행 위치는 homepc `C:\side_PJT\Reels\threads-agent\` (여기는 기록용 사본).

| 파일 | 역할 |
|---|---|
| `threads_login.py` | 전용 크롬 프로필(`%LOCALAPPDATA%\afterwork-threads-profile`)에 로그인 (로그인 풀렸을 때만) |
| `threads_web_agent.py` | 20초마다 Worker `/threads/job` 확인 → 웹 임시저장 → `/threads/job/done`. 15분마다 새 게시글을 찾아 임시저장본과 비교 → `/threads/learn` |
| `install_agent.ps1` | 로그온 시 자동 실행 등록(작업 스케줄러 `AfterworkThreadsAgent`) + 지금 실행 |

`.env` (커밋 안 함): `WORKER_URL`, `THREADS_AGENT_KEY`(Worker 비밀값과 같은 값), 선택 `THREADS_HANDLE`.

수동 실행: `python threads_web_agent.py --once` (작업 하나) · `--watch` (게시글 비교 한 번)
로그·실패 화면: `agent.log`, `last_error.png`
