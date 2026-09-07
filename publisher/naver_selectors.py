"""네이버 SmartEditor ONE 셀렉터 — 전부 여기 한 곳에.

에디터 DOM 은 공개 문서가 없고 클래스명이 해시(`save_btn__aBc12`)라서 언제든
바뀔 수 있다. 깨졌을 때 **이 파일 하나만** 고치면 되도록 모아 둔다.

각 항목은 후보 리스트다. 위에서부터 시도해 처음 보이는 것을 쓴다.
텍스트 기반 셀렉터를 우선에 두는 이유는 해시 클래스보다 오래 살기 때문이다.

파일명 주의: publisher/selectors.py 로 두면 표준 라이브러리 selectors 를
가려서 subprocess import 가 깨진다. 이름을 되돌리지 말 것.

고칠 때:
    python publisher/naver_draft.py --keep-open --verbose
로 띄워 놓고 개발자도구에서 실제 DOM 을 보고 후보를 추가한다.
"""

# 에디터가 들어 있는 iframe
EDITOR_FRAME = "#mainFrame"

# 로그인 상태 확인 (에디터 프레임 안에서 본문 영역이 잡히면 로그인된 것)
LOGGED_IN_HINT = [
    ".se-content",
    ".se-main-container",
]

# "이전에 작성 중인 글이 있습니다" 등 시작 팝업의 '취소/새로 작성'
POPUP_DISMISS = [
    "button.se-popup-button-cancel",
    ".se-popup-button-cancel",
    "button:has-text('취소')",
    "button:has-text('새로 작성')",
]

# 도움말/공지 레이어 닫기
HELP_CLOSE = [
    "button.se-help-panel-close-button",
    ".se-help-panel-close-button",
    "button[class*='close'][class*='help']",
]

# 제목 입력 영역
TITLE = [
    ".se-documentTitle .se-text-paragraph",
    ".se-section-documentTitle .se-text-paragraph",
    ".se-documentTitle span.se-placeholder",
]

# 본문 입력 영역
BODY = [
    ".se-component-content .se-text-paragraph",
    ".se-main-container .se-text-paragraph",
    ".se-content [contenteditable='true']",
]

# 사진 넣기 버튼 (파일 선택 대화상자를 여는 쪽)
IMAGE_BUTTON = [
    "button.se-image-toolbar-button",
    "button[data-name='image']",
    "button[title='사진']",
    ".se-toolbar-item-image button",
]

# 업로드된 이미지 컴포넌트 (업로드 완료 판정에 개수를 센다)
IMAGE_COMPONENT = ".se-image"

# 임시저장 버튼. **'발행' 이 들어간 셀렉터는 이 파일에 절대 넣지 않는다.**
SAVE = [
    "button.save_btn__bzc5B",
    "button[class*='save_btn']",
    "button:text-is('저장')",
]

# 임시저장 성공 표시 (토스트 또는 저장 개수 배지)
SAVE_CONFIRM = [
    ".se-toast:has-text('저장')",
    "button[class*='save_btn'] span[class*='count']",
    "text=저장되었습니다",
]

# 클릭이 금지된 문구. naver_draft.assert_not_publish 가 이걸로 막는다.
FORBIDDEN_CLICK_TEXT = ("발행", "공개", "게시")
