@echo off
REM 매일 실행: posts/ 의 최신 글을 네이버 임시저장까지 올린다.
REM 발행은 하지 않는다 — 사람이 확인하고 직접 누른다.
REM
REM Windows 작업 스케줄러 등록 시 반드시 "사용자가 로그온한 경우에만 실행" 을
REM 고를 것. 헤드풀 브라우저라 데스크톱 세션이 필요하다.

setlocal
chcp 65001 >nul

REM 저장소를 클론한 경로로 바꾸세요
cd /d C:\side_PJT\Blog_flight\flight-deals
if errorlevel 1 (
  echo [오류] 저장소 경로를 찾을 수 없습니다. run_draft.bat 의 cd 경로를 고치세요.
  exit /b 1
)

if not exist publisher\logs mkdir publisher\logs
python publisher\naver_draft.py >> publisher\logs\draft.log 2>&1
set RC=%ERRORLEVEL%

if %RC% NEQ 0 (
  echo [%DATE% %TIME%] 실패 rc=%RC% >> publisher\logs\draft.log
)
exit /b %RC%
