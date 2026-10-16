"""
판정 엔진.

시트의 CHECK / ABSENT 를 실제 로그에 대해 판정한다.
TIMEOUT 기준 시각은 두 가지다.
  ACTION    : 마지막 자극이 완료된 시각
  PREV_STEP : 직전 스텝이 매칭된 시각
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field


def _wall() -> float:
    return time.monotonic()

from .dut import Dut, DutTimeout
from .events import NullWriter
from .spec import Step, TestCase


@dataclass
class StepResult:
    step: Step
    passed: bool
    at: float | None = None
    line: str = ""
    reason: str = ""


@dataclass
class TcResult:
    tc: TestCase
    results: list[StepResult] = field(default_factory=list)
    absent_hits: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results) and not self.absent_hits

    def report(self) -> str:
        out = [f"{self.tc.tc_id} {self.tc.name}"]
        for r in self.results:
            mark = "PASS" if r.passed else "FAIL"
            when = f"{r.at:8.3f}s" if r.at is not None else "   --   "
            out.append(f"  [{mark}] step{r.step.no} {when} {r.step.method}")
            if not r.passed:
                out.append(f"         기대: {r.step.match} {r.step.value!r}")
                out.append(f"         사유: {r.reason}")
        for h in self.absent_hits:
            out.append(f"  [FAIL] ABSENT 위반: {h}")
        return "\n".join(out)


def _matcher(step: Step):
    if step.match == "REGEX":
        rx = re.compile(step.value)
        return lambda s: rx.search(s) is not None
    return lambda s: step.value in s


def run_tc(dut: Dut, tc: TestCase, default_timeout: float = 60.0,
           ev=None) -> TcResult:
    """
    자극을 실행하고, 스텝 순서대로 로그를 판정한다.
    ABSENT 는 TC 실행 구간 전체를 훑어 사후 검사한다.
    """
    ev = ev or NullWriter()
    result = TcResult(tc=tc)

    if getattr(dut, "no_stimulus", False):
        # 관찰 모드. 자극을 주지 않고 지금 흐르는 로그만 판정한다.
        # 에이징을 끊지 않고 연결·로그인·판정 경로를 확인할 때 쓴다.
        # 부팅 관련 TC 는 당연히 타임아웃으로 실패한다.
        for act in tc.actions:
            ev.emit("action", tc_id=tc.tc_id, action=act.value, phase="skipped")
        dut.mark_action_done()
    else:
        # 자극 실행 중 지나간 로그를 모아둔다. 자극이 확인용으로
        # 소비한 줄(SOFT_CYCLE 의 SHUTDOWN 등)을 CHECK 가 다시 볼 수 있어야 한다.
        dut.action_buf = []
        dut._recording = True                                # noqa: SLF001
        try:
            for act in tc.actions:
                ev.emit("action", tc_id=tc.tc_id, action=act.value, phase="start")
                dut.do_action(act.value)
                ev.emit("action", tc_id=tc.tc_id, action=act.value, phase="done")
        finally:
            dut._recording = False                           # noqa: SLF001
        if not tc.actions:
            # 이 TC 는 자체 ACTION 이 없다 - dut.last_action_t 를 그대로 두면
            # 직전 TC 가 액션을 끝낸 시각(심지어 그 TC 의 타임아웃 예산까지
            # 다 써버린 시각)을 그대로 물려받아, TIMEOUT_FROM=ACTION 기준
            # 시각이 이미 지나있어 폴링을 시작하기도 전에 즉시 실패한다
            # (2026-09-30 실측: TC013→014→015 뒤에 오는 TC016/017/018 이
            # duration_s<1s 로 매번 즉시 미검출 처리됨). "액션 없음" 도
            # no_stimulus 분기처럼 지금을 기준 시각으로 새로 잡는다.
            dut.mark_action_done()

    checks = tc.checks
    absents = tc.absents
    absent_m = [(s, _matcher(s)) for s in absents]

    action_t = dut.last_action_t       # TIMEOUT_FROM=ACTION 의 기준 (고정)
    prev_t = action_t                  # TIMEOUT_FROM=PREV_STEP 의 기준 (매 스텝 갱신)
    ref_t = action_t
    pending = list(checks)
    fail_rx = {
        s.value: re.compile(re.escape(s.fail)) for s in checks if s.fail
    }

    if tc.ordered:
        for step in pending:
            # ANY       = 로그 전체 구간. 자극 없이 서버/외부가 일으키는 이벤트용
            # ACTION    = 마지막 자극 완료 시각
            # PREV_STEP = 직전 스텝이 매칭된 시각
            if step.timeout_from == "ANY":
                base = 0.0
            elif step.timeout_from == "PREV_STEP":
                base = prev_t
            else:
                base = action_t
            to = (float("inf") if step.timeout_from == "ANY"
                  else (step.timeout if step.timeout is not None else default_timeout))
            ev.emit("step_wait", tc_id=tc.tc_id, step=step.no,
                    method=step.method, value=step.value,
                    timeout=None if to == float("inf") else to,
                    base=step.timeout_from)
            if step.type == "SHELL_CHECK":
                r = _shell_check(dut, step)
            else:
                r = _await_one(dut, step, base, to, absent_m, result, fail_rx, ev)
            ev.emit("step_done", tc_id=tc.tc_id, step=step.no,
                    passed=r.passed, at=r.at, reason=r.reason)
            if r.passed and step.capture:
                key, val = _capture(step, r.line)
                if key:
                    ev.emit("capture", tc_id=tc.tc_id, key=key, value=val)
            result.results.append(r)
            if not r.passed:
                # 앞 스텝이 실패하면 뒤 스텝은 기준 시각이 없다
                for rest in pending[pending.index(step) + 1:]:
                    result.results.append(StepResult(
                        rest, False, reason="앞 스텝 실패로 미실행"))
                break
            # 매칭에 성공하면 항상 갱신한다. 그래야 다음 스텝의
            # PREV_STEP 기준이 "직전 스텝의 매칭 시각"이 된다.
            prev_t = r.at if r.at is not None else prev_t
    else:
        # 순서 무관: 전체 구간에서 각각 독립적으로 찾는다
        # SHELL_CHECK 는 아직 이 경로를 지원하지 않는다 (로그 스트림이
        # 아니라 셸 명령이라 _await_many 의 '전체 구간에서 찾기' 개념이
        # 안 맞는다). ORDERED=Y 인 TC 에서만 쓸 것.
        window = max(
            (s.timeout if s.timeout is not None else default_timeout)
            for s in pending
        ) if pending else default_timeout
        found = _await_many(dut, pending, ref_t, window, absent_m, result, ev)
        result.results.extend(found)

    return result


def _capture(step, line: str) -> tuple[str | None, str | None]:
    """
    CAPTURE 컬럼: "이름" 또는 "이름:정규식".
    정규식이 있으면 첫 그룹을, 없으면 매칭된 줄 전체를 저장한다.
    """
    spec_ = step.capture or ""
    name, _, rx = spec_.partition(":")
    name = name.strip()
    if not name:
        return None, None
    if rx.strip():
        m = re.search(rx.strip(), line)
        return (name, m.group(1) if m and m.groups() else None)
    return name, line.strip()


def _shell_check(dut, step, retries: int = 3, retry_gap_s: float = 2.0) -> StepResult:
    """
    UART 로그가 아니라 장비에 직접 셸 명령을 실행해 결과를 확인한다.
    설정 변경(SHELL 액션 + 리부팅) 후 실제로 config 파일에 반영됐는지
    같은, 로그에는 안 남고 파일/명령 출력으로만 확인 가능한 항목용이다.

    VALUE 컬럼 형식: "<셸 명령>::<기대 문자열 또는 정규식>"
    MATCH 컬럼으로 CONTAINS/REGEX 를 고른다 (CHECK 와 동일).

    리부팅 직후엔 파일 반영이 살짝 늦을 수 있어 짧게 재시도한다.
    """
    if not hasattr(dut, "shell"):
        return StepResult(step, False,
                          reason="SHELL_CHECK 는 live 모드에서만 실행할 수 있습니다 "
                                 "(offline/오프라인 재생 지원 안 함)")

    cmd, sep, expect = step.value.partition("::")
    if not sep:
        return StepResult(step, False,
                          reason=f"SHELL_CHECK VALUE 형식 오류 (명령::기대값 이어야 함): {step.value!r}")
    cmd, expect = cmd.strip(), expect.strip()
    matches = (lambda s: re.search(expect, s) is not None) if step.match == "REGEX" \
        else (lambda s: expect in s)

    last_out = ""
    for attempt in range(retries):
        try:
            _, last_out = dut.shell(cmd, timeout=20, check=False)
        except Exception as e:                                # noqa: BLE001
            last_out = f"(셸 실행 실패: {e})"
        if matches(last_out):
            return StepResult(step, True, at=time.monotonic(), line=last_out[:200])
        if attempt < retries - 1:
            time.sleep(retry_gap_s)

    return StepResult(step, False,
                      reason=f"SHELL_CHECK {retries}회 재시도 후에도 실패. "
                             f"명령: {cmd!r} / 기대: {expect!r} / "
                             f"마지막 출력: {last_out[:200]!r}")


def _await_one(dut, step, base_t, timeout, absent_m, result, fail_rx, ev=None) -> StepResult:
    ok = _matcher(step)
    fail_pat = fail_rx.get(step.value)
    deadline = base_t + timeout
    label = "로그 전체" if timeout == float("inf") else f"{timeout:.0f}s"

    ev = ev or NullWriter()
    last_beat = 0.0

    # 자극 중에 지나간 줄부터 확인한다. 없으면 새 줄을 기다린다.
    # 버퍼에는 이 TC 의 자극 구간 로그만 들어 있으므로 시각으로 거르지 않는다.
    # SOFT_CYCLE 의 SHUTDOWN 은 자극 완료 시각보다 46초 앞서 나온다.
    for line in list(getattr(dut, "action_buf", [])):
        _scan_absent(line, absent_m, result)
        if fail_pat and fail_pat.search(line.text):
            return StepResult(step, False, at=line.t, line=line.text,
                              reason=f"FAIL 문구 검출: {step.fail!r}")
        if ok(line.text):
            return StepResult(step, True, at=line.t, line=line.text)

    while True:
        line = dut.source.next_line(0.5)
        now = dut.source.now()
        # 몇 초째 무엇을 기다리는 중인지 주기적으로 알린다.
        # 이게 없으면 타임아웃 날 때까지 화면에 아무것도 안 뜬다.
        #
        # 주기는 벽시계로 잰다. 오프라인 재생은 로그 시각이 빨리 흘러서
        # dut.source.now() 로 재면 이벤트가 수만 건씩 쌓인다.
        if _wall() - last_beat >= 2.0:
            last_beat = _wall()
            ev.emit("waiting", step=step.no, value=step.value,
                    elapsed=round(now - base_t, 1),
                    timeout=None if timeout == float("inf") else timeout)
        if line is None:
            if now > deadline:
                return StepResult(step, False,
                                  reason=f"{label} 내 미검출")
            continue
        _scan_absent(line, absent_m, result)

        if fail_pat and fail_pat.search(line.text):
            return StepResult(step, False, at=line.t, line=line.text,
                              reason=f"FAIL 문구 검출: {step.fail!r}")
        if ok(line.text):
            return StepResult(step, True, at=line.t, line=line.text)
        if line.t > deadline:
            return StepResult(step, False, reason=f"{label} 내 미검출")

def _await_many(dut, steps, base_t, window, absent_m, result, ev=None) -> list[StepResult]:
    from .events import NullWriter
    ev = ev or NullWriter()
    remaining = {s.value: (s, _matcher(s)) for s in steps}
    hits: dict[str, StepResult] = {}
    deadline = base_t + window

    # 자극 중에 지나간 줄부터 확인한다. _await_one 과 같은 이유다.
    # 이게 없으면 자극이 소비한 줄을 쓰는 TC 는 영영 통과하지 못한다.
    # (RECORD OFF 는 SHUTDOWN 직전, MICOM_INFO 는 BOOT 직후에 나온다)
    for line in list(getattr(dut, "action_buf", [])):
        _scan_absent(line, absent_m, result)
        for key, (step, ok) in list(remaining.items()):
            if ok(line.text):
                hits[key] = StepResult(step, True, at=line.t, line=line.text)
                del remaining[key]

    while remaining:
        line = dut.source.next_line(0.5)
        if line is None:
            if dut.source.now() > deadline:
                break
            continue
        _scan_absent(line, absent_m, result)
        for key, (step, ok) in list(remaining.items()):
            if ok(line.text):
                hits[key] = StepResult(step, True, at=line.t, line=line.text)
                del remaining[key]
        if line.t > deadline:
            break

    for step in steps:
        if step.value not in hits:
            hits[step.value] = StepResult(step, False,
                                          reason=f"{window:.0f}s 내 미검출")
        # 순서 무관 TC 도 이벤트를 남긴다. 지금은 events.jsonl 에
        # 이 TC 들만 진행 상세가 비어 있어 실패 분석이 안 된다.
        r = hits[step.value]
        ev.emit("step_wait", tc_id=result.tc.tc_id, step=step.no,
                method=step.method, value=step.value,
                timeout=window, base="ACTION(unordered)")
        ev.emit("step_done", tc_id=result.tc.tc_id, step=step.no,
                passed=r.passed, at=r.at, reason=r.reason)
    return [hits[s.value] for s in steps]

def _scan_absent(line, absent_m, result) -> None:
    for step, ok in absent_m:
        if ok(line.text):
            result.absent_hits.append(f"{step.value!r} @ {line.t:.3f}s :: {line.text[:100]}")
