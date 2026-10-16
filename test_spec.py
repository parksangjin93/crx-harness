"""
시트 기반 테스트.

테스트 함수는 하나뿐이다. 케이스는 시트에서 온다.
시트를 고치면 다음 실행에 바로 반영된다. 코드 생성이 없으므로
진실의 소스가 하나다.
"""

from __future__ import annotations

import pytest

from harness.dut import DutTimeout, InfraError
from harness.verdict import run_tc


def test_case(dut, case, dut_config, forbidden, record_property, ev):
    if case.blocked_by:
        pytest.skip(f"blocked: {case.blocked_by}")

    record_property("tc_id", case.tc_id)
    record_property("tags", ",".join(case.tags))
    ev.emit("tc_start", tc_id=case.tc_id, name=case.name,
            steps=len(case.checks), tags=case.tags)

    try:
        result = run_tc(dut, case,
                        default_timeout=dut_config.default_timeout_s, ev=ev)
    except InfraError:
        raise
    except DutTimeout as e:
        pytest.fail(f"{case.tc_id} 자극 실패: {e}", pytrace=False)

    if result.results and result.results[0].at is not None:
        record_property("first_hit_s", round(result.results[0].at, 3))

    # 통과/실패와 무관하게 스텝별 상세(무엇을 기대했고, 실제로 어떤 줄을
    # 언제 잡았는지)를 남긴다 - 사람이 나중에 "어떻게 확인됐는지" 보려면
    # 실패 사유만으로는 부족하고, 통과한 스텝의 근거도 있어야 한다.
    ev.emit("tc_steps", tc_id=case.tc_id,
            steps=[{
                "no": r.step.no, "type": r.step.type, "method": r.step.method,
                "match": r.step.match, "value": r.step.value,
                "passed": r.passed, "at": r.at, "line": r.line,
                "reason": r.reason,
            } for r in result.results],
            absent_hits=list(result.absent_hits))

    if not result.passed:
        pytest.fail(result.report(), pytrace=False)


@pytest.fixture(autouse=True)
def check_forbidden(request, dut, forbidden):
    """
    autouse. 모든 테스트에 자동 적용된다.
    테스트 본문이 통과해도 금지 패턴이 나오면 실패시킨다.

    known_issues.yaml 에 등록된 항목은 제외한다.
    이 제외가 없으면 tp2802 Call trace 로 100% 실패한다.
    """
    # 이 TC 가 시작되는 지점을 기록한다. 파일 전체를 훑으면 앞선 TC 의
    # 검출이 계속 다시 세어져서, 이미 조치한 것도 뒤 TC 를 계속 실패시킨다.
    mark = _mark(dut)
    yield
    excludes = getattr(pytest, "_forbidden_excludes", [])
    lines = _collect_since(dut, mark)
    hits: list[str] = []

    for i, text in enumerate(lines):
        for rx, desc in forbidden:
            if not rx.search(text):
                continue
            # 커널 스택 트레이스는 여러 줄에 걸쳐 있다.
            # 'Call trace:' 줄 자체에는 드라이버 이름이 없으므로
            # 줄 단위로 제외하면 known issue 가 걸러지지 않는다.
            # 앞뒤 문맥까지 보고 판단한다.
            ctx = "\n".join(lines[max(0, i - 5): i + CONTEXT_LINES])
            if any(x.search(ctx) for x in excludes):
                continue
            hits.append(f"[{desc}] {text[:120]}")

    if hits:
        uniq = list(dict.fromkeys(hits))[:15]
        pytest.fail(
            f"금지 패턴 {len(hits)}건 검출 (중복 제거 {len(uniq)}종):\n"
            + "\n".join(uniq), pytrace=False)


# 스택 트레이스 한 블록의 최대 길이. 실측상 tp2802 트레이스가 약 30줄이다.
CONTEXT_LINES = 40


def _mark(dut):
    """
    현재 위치를 기록한다.
    live 는 로그 파일의 바이트 오프셋, offline 은 재생 인덱스.
    파일이 커도 이 지점부터만 읽으므로 비용이 일정하다.
    """
    src = dut.source
    if hasattr(src, "_lines"):            # FileLogSource
        return ("idx", getattr(src, "_i", 0))
    art = getattr(src, "artifact", None)
    try:
        return ("pos", str(art), art.stat().st_size if art else 0)
    except OSError:
        return ("pos", str(art), 0)


def _collect_since(dut, mark) -> list[str]:
    src = dut.source
    if mark[0] == "idx":
        lines = [ln.text for ln in getattr(src, "_lines", [])]  # noqa: SLF001
        return lines[max(0, mark[1] - 5):]

    _, path, pos = mark
    art = getattr(src, "artifact", None)
    cur = str(art) if art else path
    if cur != path:
        # 로그가 회전했다. 새 파일은 통째로 본다.
        pos = 0
    from pathlib import Path as _Path
    p = _Path(cur)
    if not p.exists():
        return []
    with p.open("rb") as f:
        f.seek(max(0, pos - 2000))
        data = f.read()
    return data.decode("utf-8", errors="replace").splitlines()
