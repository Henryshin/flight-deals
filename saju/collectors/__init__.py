"""수집기 레지스트리.

collector/__init__.py 가 비어 있는 것과 달리 여기엔 레지스트리를 둔다 —
collect_saju.py 가 --collector 이름 하나로 갈아끼울 수 있어야 하기 때문.
"""
from .base import (  # noqa: F401  (재수출)
    MODE_HASHTAG, MODE_KEYWORD, MODE_TAG,
    STATUS_AUTH, STATUS_ERROR, STATUS_NO_RESULTS, STATUS_OK, STATUS_PARSE,
    STATUS_PERMISSION, STATUS_QUOTA, STATUS_RATE_LIMITED, STATUS_TIMEOUT,
    CollectResult, Collector, RawPost,
)
from .instagram import InstagramCollector
from .sample import SampleCollector
from .threads import ThreadsCollector

COLLECTORS = {
    SampleCollector.name: SampleCollector,
    ThreadsCollector.name: ThreadsCollector,
    InstagramCollector.name: InstagramCollector,
}


def get_collector(name, **kwargs):
    try:
        cls = COLLECTORS[name]
    except KeyError:
        known = ", ".join(sorted(COLLECTORS))
        raise KeyError(f"알 수 없는 수집기 '{name}'. 사용 가능: {known}") from None
    return cls(**kwargs)
