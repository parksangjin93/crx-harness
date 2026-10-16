"""
이벤트 기록기.

pytest 와 GUI 를 직접 붙이지 않는다. 하네스는 JSONL 로 이벤트를 흘리고
GUI 는 그 파일을 tail 한다. 그래서

  - pytest 쪽 수정이 훅 몇 줄로 끝난다
  - GUI 가 죽어도 테스트는 계속 돈다. 반대도 마찬가지다
  - CI 에서 돌린 결과도 같은 포맷이면 같은 뷰어로 본다

UART 원문은 여기 넣지 않는다. 초당 수백 줄이라 이벤트 스트림이 묻힌다.
로그는 artifacts/uart.log 를 GUI 가 따로 tail 한다.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path


class EventWriter:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")
        self._lock = threading.Lock()

    def emit(self, kind: str, **fields) -> None:
        rec = {"ts": time.time(), "kind": kind, **fields}
        line = json.dumps(rec, ensure_ascii=False)
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()      # GUI 가 즉시 봐야 한다

    def close(self) -> None:
        with self._lock:
            self._fh.close()


class NullWriter:
    """이벤트 기록을 끈 상태. 하네스 단독 실행에 쓴다."""

    def emit(self, kind: str, **fields) -> None:  # noqa: D102
        pass

    def close(self) -> None:  # noqa: D102
        pass


def read_events(path: str | Path, offset: int = 0) -> tuple[list[dict], int]:
    """
    offset 바이트부터 읽어 새 이벤트만 돌려준다.
    GUI 가 폴링할 때 쓴다. 마지막 줄이 잘려 있으면 버리고
    다음 폴링에서 다시 읽는다.
    """
    p = Path(path)
    if not p.exists():
        return [], 0
    with p.open("rb") as f:
        f.seek(offset)
        chunk = f.read()
    if not chunk:
        return [], offset

    text = chunk.decode("utf-8", errors="replace")
    consumed = len(chunk)
    if not text.endswith("\n"):
        cut = text.rfind("\n")
        if cut < 0:
            return [], offset
        consumed = len(text[: cut + 1].encode("utf-8"))
        text = text[: cut + 1]

    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out, offset + consumed


def tail_lines(path: str | Path, n: int = 400) -> list[str]:
    """UART 로그의 마지막 n 줄. 128MB 파일도 끝에서만 읽는다."""
    p = Path(path)
    if not p.exists():
        return []
    size = p.stat().st_size
    block = min(size, max(n * 200, 65536))
    with p.open("rb") as f:
        f.seek(size - block)
        data = f.read()
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[-n:]
