"""
pytest 공통 설정.

두 가지 모드로 같은 테스트를 돌린다.
  --mode live     실제 DUT (COM 포트 + 릴레이)
  --mode offline  캡처된 로그 파일 재생 (판정식 검증용)

먼저 offline 으로 판정식을 잡고 나서 live 로 넘어간다.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
import yaml
from datetime import datetime

from harness import spec
from harness.dut import DutConfig, InfraError, OfflineDut, UartDut
from harness.pdu import DEFAULT_URL, PduClient, PduError, PduRelay
from harness.events import EventWriter, read_events
from harness.logsource import DaemonLogSource, DaemonUnavailable, FileLogSource, SerialLogSource
from harness.store import Store

ROOT = Path(__file__).parent
ARTIFACTS = ROOT / "artifacts"


def pytest_addoption(parser):
    parser.addoption("--mode", default="offline", choices=["live", "offline"])
    parser.addoption("--spec", default=str(ROOT / "spec_v2.xlsx"))
    parser.addoption("--dut", default=None, help="config/duts.yaml 의 DUT 이름")
    parser.addoption("--no-stimulus", action="store_true",
                     help="자극을 주지 않고 흐르는 로그만 판정 (에이징 유지)")
    parser.addoption("--dut-config", default=str(ROOT / "config" / "duts.yaml"))
    parser.addoption("--replay", default=None, help="offline 모드에서 재생할 로그")
    parser.addoption("--product", default=None, help="시트의 PRODUCT 필터 (기본: 프로파일 값)")
    parser.addoption("--source", default="direct", choices=["direct", "daemon"],
                     help="live 모드에서 포트를 직접 열지(direct, 기본), "
                          "crx-continuous 상시 프로그램에 접속할지(daemon)")


def pytest_configure(config):
    if config.getoption("--mode") == "live":
        from harness.secrets import HOWTO, dut_password
        if not dut_password(_dut_raw(config).get("name")):
            raise SystemExit(HOWTO)
    config.addinivalue_line("markers", "infra: 장비 문제로 실패 (FW 결함 아님)")
    config.addinivalue_line("markers", "blocked: 자극 수단 미비로 실행 불가")

    # DUT 마다, 실행마다 폴더를 분리한다.
    art = _artifact_dir(config)
    art.mkdir(parents=True, exist_ok=True)
    config._art = art                                       # noqa: SLF001
    ev_path = art / "events.jsonl"
    config._ev = EventWriter(ev_path)                       # noqa: SLF001
    print(f"[{_dut_raw(config).get('name')}] 산출물 -> {art}")
    cfg = _dut_raw(config)
    config._ev.emit("run_start",
                    mode=config.getoption("--mode"),
                    product=config.getoption("--product") or cfg.get("product", "CRX"),
                    dut=cfg.get("name", "dut"),
                    spec=Path(config.getoption("--spec")).name,
                    replay=config.getoption("--replay") or None)


def pytest_unconfigure(config):
    ev = getattr(config, "_ev", None)
    if ev is None:
        return
    ev.emit("run_end")
    ev.close()
    # 실행이 끝나면 SQLite 로 옮긴다. 추이는 누적돼야 보인다.
    art = getattr(config, "_art", ARTIFACTS)                 # noqa: SLF001
    raw = _dut_raw(config)
    try:
        events, _ = read_events(art / "events.jsonl")
        Store(ARTIFACTS / "runs.db").ingest(events)          # DB 는 4대 공용
    except Exception as e:                                   # noqa: BLE001
        print(f"[warn] 결과 적재 실패: {e}")
    try:
        from harness.report import write_summary
        p = write_summary(art, raw.get("name", "dut"), raw.get("product", "?"))
        print(f"\n요약: {p}")
        print(p.read_text(encoding="utf-8"))
    except Exception as e:                                   # noqa: BLE001
        print(f"[warn] 요약 생성 실패: {e}")


@pytest.fixture(scope="session")
def ev(request):
    return request.config._ev                                # noqa: SLF001


# --------------------------------------------------------------------------
# 설정
# --------------------------------------------------------------------------

def _dut_raw(config) -> dict:
    """duts.yaml (여러 대) 과 dut_crx.yaml (한 대) 을 모두 받는다."""
    raw = yaml.safe_load(Path(config.getoption("--dut-config")).read_text(encoding="utf-8"))
    if "duts" not in raw:
        return raw
    want = config.getoption("--dut")
    entries = raw["duts"]
    if want:
        hit = [d for d in entries if d.get("name") == want]
        if not hit:
            names = [d.get("name") for d in entries]
            raise SystemExit(f"--dut {want!r} 없음. 있는 DUT: {names}")
        entry = hit[0]
    else:
        entry = entries[0]
    return {**(raw.get("defaults") or {}), **entry}


def _pdu_cfg(config) -> dict:
    """duts.yaml 상위의 pdu: 블록. 없으면 빈 dict 를 준다.

    DUT 항목이 아니라 랙 전체에 하나뿐인 설정이라 _dut_raw 와 분리한다.
    """
    p = Path(config.getoption("--dut-config"))
    if not p.exists():
        return {}
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return raw.get("pdu") or {}


def _artifact_dir(config) -> Path:
    """
    실행 하나 = 폴더 하나.
    UART 로그와 판정 결과를 같은 곳에 둔다. 따로 두면 실패 분석할 때
    두 폴더를 오가며 시각을 맞춰야 한다.

        <log_dir 또는 artifacts/<dut>>/2026-09-08-14시23분05초/
    """
    raw = _dut_raw(config)
    base = Path(raw["log_dir"]) if raw.get("log_dir") else \
        ARTIFACTS / raw.get("name", "dut")
    stamp = datetime.now().strftime("%Y-%m-%d-%H시%M분%S초")
    return base / stamp


@pytest.fixture(scope="session")
def dut_config(request) -> DutConfig:
    raw = _dut_raw(request.config)
    from harness.secrets import dut_password
    raw["login_password"] = dut_password(raw.get("name"))
    # 오프라인 재생은 하드웨어가 필요 없다. 포트를 찾지 않는다.
    if request.config.getoption("--mode") == "live" and raw.get("port_match"):
        from harness.ports import resolve
        raw["serial_port"] = resolve(raw["port_match"], raw.get("serial_port"))
        print(f"[{raw.get('name')}] 포트 확인: {raw['serial_port']}")
    known = {f.name for f in DutConfig.__dataclass_fields__.values()}
    return DutConfig(**{k: v for k, v in raw.items() if k in known})


@pytest.fixture(scope="session")
def product(request, dut_config) -> str:
    return request.config.getoption("--product") or dut_config.product


@pytest.fixture(scope="session")
def forbidden() -> list[tuple[re.Pattern, str]]:
    cfg = yaml.safe_load((ROOT / "config" / "forbidden.yaml").read_text(encoding="utf-8"))
    ki = yaml.safe_load((ROOT / "config" / "known_issues.yaml").read_text(encoding="utf-8"))
    excludes = [re.compile(k["pattern"]) for k in (ki.get("known_issues") or [])]
    out = []
    for f in cfg["forbidden"]:
        out.append((re.compile(f["pattern"]), f["desc"]))
    pytest._forbidden_excludes = excludes      # noqa: SLF001
    return out


# --------------------------------------------------------------------------
# 시트 -> 테스트 파라미터화
# --------------------------------------------------------------------------

def pytest_generate_tests(metafunc):
    if "case" not in metafunc.fixturenames:
        return
    path = metafunc.config.getoption("--spec")
    cases = spec.load(path)
    raw = yaml.safe_load(Path(metafunc.config.getoption("--dut-config")).read_text(encoding="utf-8"))
    want = metafunc.config.getoption("--dut")
    if "duts" in raw:
        entries = raw["duts"]
        e = next((d for d in entries if d.get("name") == want), entries[0])
        default_prod = e.get("product", "CRX")
    else:
        default_prod = raw.get("product", "CRX")
    prod = metafunc.config.getoption("--product") or default_prod

    selected, ids = [], []
    for tc in cases:
        if not tc.applies_to(prod):
            continue
        if not tc.checks:
            continue           # 검증 조건이 없는 행 (Jira 원문) 은 생성하지 않는다
        selected.append(tc)
        ids.append(f"{tc.tc_id}-{tc.name[:28]}")
    metafunc.parametrize("case", selected, ids=ids)


def pytest_collection_modifyitems(items):
    """
    회귀 감시용(regression_watch) TC 는 원인(FW 버그)이 안 고쳐지는 한
    실패가 평상시 상태다. xfail(strict=True) 로 표시해두면:
      - 예상대로 실패 -> XFAIL 로 집계, 전체 종료 코드(rc)에 영향 없음
        (run_all.py 가 "성공"/"실패" 를 가릴 때 이게 유일한 실패 원인이면
        이제 성공으로 뜬다).
      - 반대로 어느 날 실제로 통과해버리면 strict=True 때문에 그 자체가
        실패로 뜬다 - FW 가 고쳐졌다는 신호를 놓치지 않기 위함이다.
    """
    for item in items:
        case = item.callspec.params.get("case") if hasattr(item, "callspec") else None
        if case is not None and "regression_watch" in getattr(case, "tags", []):
            item.add_marker(pytest.mark.xfail(
                reason=f"{case.tc_id}: 회귀 감시 중 - FW 수정 전까지 실패가 정상 상태",
                strict=True))


# --------------------------------------------------------------------------
# DUT
# --------------------------------------------------------------------------

@pytest.fixture(scope="session")
def dut(request, dut_config):
    mode = request.config.getoption("--mode")
    art = getattr(request.config, "_art", ARTIFACTS)         # noqa: SLF001

    if mode == "offline":
        log = request.config.getoption("--replay")
        if not log:
            pytest.fail("offline 모드에는 --replay 이 필요합니다", pytrace=False)
        src = FileLogSource(log)
        src.start()
        yield OfflineDut(dut_config, src)
        src.stop()
        return

    raw = _dut_raw(request.config)
    source_mode = request.config.getoption("--source")

    if source_mode == "daemon":
        # 4단계: 포트를 직접 안 열고 crx-continuous 상시 프로그램에 접속한다.
        # 다른 접속(사람의 수동 입력)이 이미 쓰고 있으면 DaemonUnavailable 이
        # 올라오고, 그걸 그대로 skip 으로 바꾼다 - 이게 "수동 테스트 중인
        # 기기는 건너뜀" 규칙의 실제 구현이다.
        pdu_ch = raw.get("pdu_channel")
        if pdu_ch is None:
            pytest.skip(f"'{dut_config.name}' 은 pdu_channel 이 없어 "
                       "상시 프로그램 접속 포트를 정할 수 없습니다")
        src = DaemonLogSource(Path(raw["log_dir"]), pdu_ch, run_dir=art)
        try:
            src.start()
        except DaemonUnavailable as e:
            pytest.skip(str(e))
    else:
        src = SerialLogSource(dut_config.serial_port, dut_config.baudrate,
                              log_dir=art, stem="uart",
                              max_mb=int(raw.get("log_max_mb", 200)),
                              keep=int(raw.get("log_keep", 20)))
        src.start()

    # 전원 제어. CoTermPowerMon 의 PDU 엔드포인트를 직접 친다.
    # pdu_channel / pdu_alias 는 duts.yaml 항목에 있고 DutConfig 가
    # 이미 받아 왔다. 여기서는 실물과 맞는지만 확인한다.
    pcfg = _pdu_cfg(request.config)
    relay = PduRelay(PduClient(
        base_url=pcfg.get("url", DEFAULT_URL),
        timeout_s=float(pcfg.get("timeout_s", 5.0)),
        min_gap_s=float(pcfg.get("min_gap_s", 0.15)),
    ))
    if dut_config.pdu_channel:
        # 채널 번호는 맞는데 물리 대상이 바뀐 경우는 별칭으로만 잡힌다.
        # 여기서 안 잡으면 엉뚱한 DUT 전원을 끊고 원인을 한참 찾는다.
        try:
            relay.preflight({dut_config.name: dut_config.pdu_channel},
                            {dut_config.name: dut_config.pdu_alias} if
                            dut_config.pdu_alias else None)
            print(f"[{dut_config.name}] PDU CH{dut_config.pdu_channel} 확인")
        except PduError as e:
            src.stop()
            pytest.fail(f"PDU 점검 실패\n  {e}", pytrace=False)

    d = UartDut(dut_config, src, relay=relay)
    d.no_stimulus = request.config.getoption("--no-stimulus")
    if d.no_stimulus:
        print("[관찰 모드] 자극을 주지 않습니다. 부팅 관련 TC 는 실패합니다.")
    try:
        yield d
    finally:
        src.stop()


@pytest.fixture(autouse=True)
def _fail_fast_on_infra(request, dut):
    """
    장비가 안 붙으면 남은 TC 를 빨리 접는다.
    17개가 같은 이유로 30초씩 실패하면 리포트만 지저분해지고
    실제 원인은 첫 줄에 이미 나와 있다.
    """
    n = getattr(request.session, "_infra_streak", 0)
    if n >= 2:
        pytest.skip("장비 오류가 연속 발생해 이후 TC 를 건너뜁니다 "
                    "(첫 실패 사유를 확인하세요)")
    yield


@pytest.fixture(autouse=True)
def _offline_rewind(request, dut):
    """offline 모드는 TC 마다 로그 처음부터 다시 훑는다."""
    if request.config.getoption("--mode") == "offline":
        dut.source.seek_start()
    yield


# --------------------------------------------------------------------------
# 리포트: InfraError 를 FW 결함과 분리한다
# --------------------------------------------------------------------------

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if call.excinfo and isinstance(call.excinfo.value, (InfraError, DaemonUnavailable)):
        rep.sections.append(("장비 오류", str(call.excinfo.value)))
        item.add_marker(pytest.mark.infra)
        item.session._infra_streak = getattr(item.session, "_infra_streak", 0) + 1
    elif rep.when == "call" and rep.passed:
        item.session._infra_streak = 0

    if rep.when != "call":
        return
    ev_ = getattr(item.config, "_ev", None)                  # noqa: SLF001
    if ev_ is None:
        return
    case = item.callspec.params.get("case") if hasattr(item, "callspec") else None
    is_infra = call.excinfo and isinstance(call.excinfo.value, (InfraError, DaemonUnavailable))
    ev_.emit("tc_done",
             tc_id=getattr(case, "tc_id", item.name),
             name=getattr(case, "name", ""),
             tags=list(getattr(case, "tags", [])),
             outcome="infra" if is_infra else
                     ("passed" if rep.passed else
                      "skipped" if rep.skipped else "failed"),
             duration_s=round(rep.duration, 2),
             # 트레이스백 앞부분(1500자)만 자르면 docstring/코드 맥락만 남고
             # 실제 예외("E " 줄, 보통 맨 끝)가 잘려나가는 경우가 많았다
             # (2026-10 daemon 모드 InfraError 실측 - 앞부분만 잘라서
             # report.py 의 _short_reason() 이 쓸모없는 줄을 뽑았음).
             # 뒷부분을 남겨야 실제 사유가 들어온다.
             reason="" if rep.passed else str(rep.longrepr)[-2500:])


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    cases = spec.load(config.getoption("--spec"))
    raw = _dut_raw(config)
    prod = config.getoption("--product") or raw.get("product", "CRX")
    s = spec.summarize(cases, prod)
    tr = terminalreporter
    tr.write_sep("=", f"자동화 커버리지 [{raw.get('name','dut')} / {prod}]")
    tr.write_line(f"  시트 전체 TC     : {s['total']}")
    tr.write_line(f"  {prod} 대상 TC     : {s['product']}")
    tr.write_line(f"  실행 가능        : {s['runnable']}")
    tr.write_line(f"  blocked          : {s['blocked']}")
    for reason, n in s["blocked_reasons"].items():
        tr.write_line(f"      - {reason}: {n}")