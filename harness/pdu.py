"""CoTermPowerMon PDU (Modbus RTU 8ch 릴레이) 제어.

CoTermPowerMon.exe 가 띄우는 MCP HTTP 엔드포인트를 JSON-RPC 로 직접 호출한다.
MCP 클라이언트 라이브러리는 쓰지 않는다. 필요한 건 tools/call 하나뿐이다.

확인된 사실 (2026-09-10 실측):
  - 엔드포인트   POST http://127.0.0.1:8765/mcp/PDU
                 (README/CONNECT_GUIDE 는 8766 이라고 적혀 있으나 이 PC 의
                  CoTermPowerMon.ini 는 8765. 하드코딩하지 말고 설정에서 받는다)
  - handshake    불필요. initialize 없이 tools/call 이 바로 먹는다
  - 응답         {"id":N,"jsonrpc":"2.0",
                  "result":{"content":[{"type":"text","text":"<본문>"}],
                            "isError":false}}
  - power_status 본문은 JSON. ch[] 에 {n, alias, on} 이 8개 들어온다

의도적으로 구현하지 않은 것:
  - pdu_all  ... 8채널 일괄 ON/OFF. CH1~4 에 XVD302 에이징이 돌고 있어
               한 번의 오호출로 남의 시험을 죽인다. 노출 자체를 안 한다.
  - pdu_open / pdu_close ... 사람이 UI 로 여는 장비다. 하네스가 열고 닫으면
               COM96 을 두고 다툰다. 안 열려 있으면 preflight 에서 멈춘다.
"""

from __future__ import annotations

import itertools
import json
import msvcrt
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

DEFAULT_URL = "http://127.0.0.1:8765/mcp/PDU"

# 하네스가 건드려도 되는 채널. CH1~4 는 XVD302 에이징용이라 제외한다.
DEFAULT_ALLOWED_CHANNELS = frozenset({5, 6, 7, 8})


class PduError(Exception):
    """PDU 제어 실패.

    호출부(dut.py)에서 InfraError 로 감싸 올릴 것. 장비/서버 문제이지
    FW 결함이 아니므로 실패 집계에 FW 로 들어가면 안 된다.
    """


# --------------------------------------------------------------------------
# COM96 직렬화
# --------------------------------------------------------------------------

# 2026-09-21 실측: run_all.py 는 DUT 마다 별도 프로세스(subprocess.run)를
# 띄운다. threading.Lock 은 프로세스 하나 안에서만 유효해서, 4대가 각자
# "나만 조심하면 된다"고 믿는 상태로 거의 동시에 PDU 를 때릴 수 있다.
# 실측으로 trx3208/vmc8pro/xvm508 세 프로세스의 ACC_CYCLE 시작 시각이
# 3ms 이내로 겹쳤고, 그중 두 대가 'ACC Remove' 와 'SHUTDOWN' 이 통째로
# 안 잡히는 이중 실패를 냈다 (며칠간 원인을 못 찾던 간헐적 미검출의
# 근본 원인으로 보인다). 모든 하니스 프로세스가 같은 경로를 보도록
# %TEMP% 밑에 파일을 하나 두고, OS 레벨 파일 락(msvcrt.locking)으로
# 프로세스 경계를 넘는 직렬화를 건다.
_CROSS_PROCESS_LOCK_PATH = Path(tempfile.gettempdir()) / "crx_harness_pdu.lock"


class _SerialGate:
    """PDU 는 COM96 한 줄에 Modbus RTU 다.

    DUT 4대를 병렬로 돌리므로 여러 프로세스/스레드가 같은 시리얼 라인을
    때린다. 서버가 내부에서 직렬화하는지 확인되지 않았고, RTU 프레임이
    섞이면 엉뚱한 채널이 토글된다. 하네스 쪽에서 먼저 막는다.

    threading.Lock 은 프로세스 내부용, 파일 락은 프로세스 간 직렬화용
    이다. 하나만으로는 안 된다 (프로세스 간 겹침이 실제로 재현됐다).
    """

    def __init__(self, min_gap_s: float = 0.15) -> None:
        self._lock = threading.Lock()
        self._min_gap = min_gap_s
        self._last = 0.0
        # a+b: 없으면 만들고, 있으면 이어서 연다. 내용은 안 쓴다 - 락 대상일 뿐.
        self._cross_fh = open(_CROSS_PROCESS_LOCK_PATH, "a+b")

    def raise_gap(self, min_gap_s: float) -> None:
        """게이트는 URL 단위로 공유된다.

        여러 클라이언트가 서로 다른 간격을 요구하면 더 긴 쪽을 쓴다.
        먼저 만든 쪽이 이기면, 나중에 안전하게 늘려 잡은 값이 조용히
        무시된다.
        """
        if min_gap_s > self._min_gap:
            self._min_gap = min_gap_s

    def __enter__(self) -> "_SerialGate":
        self._lock.acquire()
        # 다른 프로세스가 이미 락을 쥐고 있으면 여기서 블로킹 대기한다.
        self._cross_fh.seek(0)
        msvcrt.locking(self._cross_fh.fileno(), msvcrt.LK_LOCK, 1)
        gap = self._min_gap - (time.monotonic() - self._last)
        if gap > 0:
            time.sleep(gap)
        return self

    def __exit__(self, *exc: Any) -> bool:
        self._last = time.monotonic()
        self._cross_fh.seek(0)
        msvcrt.locking(self._cross_fh.fileno(), msvcrt.LK_UNLCK, 1)
        self._lock.release()
        return False


_GATES: Dict[str, _SerialGate] = {}
_GATES_LOCK = threading.Lock()


def _gate_for(url: str, min_gap_s: float) -> _SerialGate:
    """같은 서버 주소면 같은 게이트를 공유한다."""
    with _GATES_LOCK:
        gate = _GATES.get(url)
        if gate is None:
            gate = _SerialGate(min_gap_s)
            _GATES[url] = gate
        else:
            gate.raise_gap(min_gap_s)
        return gate


# --------------------------------------------------------------------------
# 응답 파싱
# --------------------------------------------------------------------------

def _strip_sse(raw: str) -> str:
    """Accept 에 text/event-stream 이 있어 SSE 로 올 수도 있다.

    지금 서버는 application/json 으로 주지만, 버전이 올라가 SSE 로 바뀌어도
    깨지지 않게 해 둔다.
    """
    if not (raw.startswith("event:") or raw.startswith("data:") or "\ndata:" in raw):
        return raw
    lines = [ln[5:].strip() for ln in raw.splitlines() if ln.startswith("data:")]
    return "\n".join(lines) if lines else raw


def _parse_envelope(raw: str) -> Dict[str, Any]:
    text = _strip_sse(raw.strip())
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PduError(f"응답을 JSON 으로 못 읽음: {text[:200]!r}") from exc

    if isinstance(payload, dict) and payload.get("error"):
        raise PduError(f"JSON-RPC 오류: {payload['error']}")

    result = payload.get("result") if isinstance(payload, dict) else None
    if result is None:
        raise PduError(f"응답에 result 가 없음: {text[:200]!r}")
    return result


# --------------------------------------------------------------------------
# 클라이언트
# --------------------------------------------------------------------------

class PduClient:
    """CoTermPowerMon PDU 엔드포인트 클라이언트. 스레드 안전."""

    def __init__(
        self,
        base_url: str = DEFAULT_URL,
        timeout_s: float = 5.0,
        retries: int = 2,
        min_gap_s: float = 0.15,
        allowed_channels: Iterable[int] = DEFAULT_ALLOWED_CHANNELS,
    ) -> None:
        self.base_url = base_url
        self.timeout_s = timeout_s
        self.retries = retries
        self.allowed_channels = frozenset(int(c) for c in allowed_channels)
        self._gate = _gate_for(base_url, min_gap_s)
        self._ids = itertools.count(1)

    # -- 저수준 -----------------------------------------------------------

    def _rpc(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {"jsonrpc": "2.0", "id": next(self._ids), "method": method}
        if params is not None:
            body["params"] = params
        data = json.dumps(body).encode("utf-8")

        last_exc: Optional[BaseException] = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(
                self.base_url,
                data=data,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
            )
            try:
                with self._gate:
                    with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                        raw = resp.read().decode("utf-8", "replace")
                return _parse_envelope(raw)
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                # 재시도해도 되는 건 통신 실패뿐이다. 릴레이를 특정 상태로
                # 놓는 건 멱등이라 pdu_set 재시도도 안전하다.
                last_exc = exc
                if attempt < self.retries:
                    time.sleep(0.3 * (attempt + 1))

        raise PduError(
            f"PDU 서버 응답 없음 ({self.base_url}). "
            f"CoTermPowerMon.exe 가 떠 있는지 확인할 것: {last_exc}"
        ) from last_exc

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> str:
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        blocks = result.get("content") or []
        texts: List[str] = [
            b["text"] for b in blocks if isinstance(b, dict) and isinstance(b.get("text"), str)
        ]
        joined = "\n".join(texts).strip()
        if result.get("isError"):
            raise PduError(f"{name} 실패: {joined or result!r}")
        return joined

    # -- 상태 -------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        text = self.call_tool("power_status")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise PduError(f"power_status 본문 파싱 실패: {text[:200]!r}") from exc

    def channels(self) -> Dict[int, Dict[str, Any]]:
        """{채널번호: {"alias": str, "on": bool}}"""
        st = self.status()
        out: Dict[int, Dict[str, Any]] = {}
        for entry in st.get("ch") or []:
            try:
                n = int(entry["n"])
            except (KeyError, TypeError, ValueError):
                continue
            out[n] = {"alias": str(entry.get("alias", "")), "on": bool(entry.get("on"))}
        if not out:
            raise PduError(f"power_status 에 채널 정보가 없음: {st!r}")
        return out

    # -- 제어 -------------------------------------------------------------

    def set_channel(
        self,
        ch: int,
        on: bool,
        verify: bool = True,
        verify_timeout_s: float = 3.0,
    ) -> None:
        """채널 하나를 ON/OFF. 기본으로 상태를 되읽어 확인한다.

        되읽는 이유: pdu_set 은 Modbus 쓰기가 나갔다는 것만 알려준다.
        릴레이가 실제로 붙었는지는 별개다. killall 이 1ms 만에 0 을
        반환해서 SOFT_CYCLE 이 무너졌던 것과 같은 함정을 여기서 막는다.
        """
        ch = int(ch)
        if ch not in self.allowed_channels:
            raise PduError(
                f"CH{ch} 는 하네스 허용 채널 {sorted(self.allowed_channels)} 밖입니다. "
                f"차단했습니다."
            )
        self.call_tool("pdu_set", {"ch": ch, "on": bool(on)})
        if verify:
            self.verify_channel(ch, on, timeout_s=verify_timeout_s)

    def verify_channel(
        self,
        ch: int,
        expect_on: bool,
        timeout_s: float = 3.0,
        interval_s: float = 0.3,
    ) -> None:
        ch = int(ch)
        expect = bool(expect_on)
        deadline = time.monotonic() + timeout_s
        actual: Optional[bool] = None
        while True:
            entry = self.channels().get(ch)
            if entry is None:
                raise PduError(f"CH{ch} 가 power_status 에 없음")
            actual = entry["on"]
            if actual == expect:
                return
            if time.monotonic() >= deadline:
                break
            time.sleep(interval_s)
        raise PduError(
            f"CH{ch} 상태 불일치: 기대 {'ON' if expect else 'OFF'}, "
            f"실제 {'ON' if actual else 'OFF'} ({timeout_s}s 내 미반영)"
        )

    # -- 세션 시작 점검 ---------------------------------------------------

    def preflight(self, mapping: Optional[Mapping[str, int]] = None,
                  aliases: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
        """세션 시작 시 1회. 실패하면 테스트를 시작하지 않는다.

        mapping  {dut_name: channel}
        aliases  {dut_name: alias 에 들어 있어야 하는 문자열}
                 배선이 바뀌면 채널 번호는 그대로인데 물리 대상이 달라진다.
                 alias 로 대조해야 그걸 잡는다.
        """
        st = self.status()
        if not st.get("open"):
            raise PduError(
                f"PDU 가 열려 있지 않습니다 (port={st.get('port')!r}). "
                f"CoTermPowerMon 전원 탭에서 먼저 여세요."
            )
        if not st.get("connected"):
            raise PduError(f"PDU 통신 끊김 (port={st.get('port')!r})")

        chans = self.channels()

        for ch in sorted(self.allowed_channels):
            if ch not in chans:
                raise PduError(f"허용 채널 CH{ch} 가 장비에 없음. 채널 수 확인 필요")

        if mapping:
            seen: Dict[int, str] = {}
            for name, ch in mapping.items():
                ch = int(ch)
                if ch not in self.allowed_channels:
                    raise PduError(
                        f"{name} 의 채널 CH{ch} 가 허용 목록 "
                        f"{sorted(self.allowed_channels)} 밖입니다"
                    )
                if ch in seen:
                    raise PduError(f"CH{ch} 가 {seen[ch]} 와 {name} 에 중복 배정됨")
                seen[ch] = name

                want = (aliases or {}).get(name)
                if want:
                    got = chans[ch]["alias"]
                    if want.lower() not in got.lower():
                        raise PduError(
                            f"{name}: CH{ch} 별칭 불일치. "
                            f"기대 '{want}' 포함, 실제 '{got}'. 배선/설정 확인 필요"
                        )
        return st


# --------------------------------------------------------------------------
# DUT 한 대에 묶인 컨트롤러
# --------------------------------------------------------------------------

class PduController:
    """DUT 한 대의 전원 채널. 채널이 고정돼 있어 남의 채널을 못 건드린다.

    기존 RelayController 자리에 들어간다. 인터페이스는 set(on) 하나.
    """

    def __init__(
        self,
        client: PduClient,
        channel: int,
        expect_alias: Optional[str] = None,
        name: str = "",
    ) -> None:
        self.client = client
        self.channel = int(channel)
        self.expect_alias = expect_alias
        self.name = name or f"CH{self.channel}"
        if self.channel not in client.allowed_channels:
            raise PduError(
                f"{self.name}: CH{self.channel} 는 허용 채널 "
                f"{sorted(client.allowed_channels)} 밖입니다"
            )

    @property
    def configured(self) -> bool:
        return True

    def set(self, on: bool) -> None:
        self.client.set_channel(self.channel, on)

    def on(self) -> None:
        self.set(True)

    def off(self) -> None:
        self.set(False)

    def is_on(self) -> bool:
        entry = self.client.channels().get(self.channel)
        if entry is None:
            raise PduError(f"{self.name}: CH{self.channel} 가 power_status 에 없음")
        return bool(entry["on"])

    def check_alias(self) -> None:
        if not self.expect_alias:
            return
        entry = self.client.channels().get(self.channel)
        got = entry["alias"] if entry else ""
        if self.expect_alias.lower() not in got.lower():
            raise PduError(
                f"{self.name}: CH{self.channel} 별칭 불일치. "
                f"기대 '{self.expect_alias}' 포함, 실제 '{got}'"
            )

    def __repr__(self) -> str:
        return f"<PduController {self.name} ch={self.channel}>"


class PduRelay:
    """dut.py 의 RelayController 자리에 그대로 들어가는 어댑터.

    인터페이스가 set(channel, on) 인 것은 기존 코드에 맞춘 것이다.
    채널 번호는 DutConfig.pdu_channel 에서 온다.

    POWER_CYCLE 은 cfg.relay_channel(0~3) 을 넘기므로 여기서 막힌다.
    막히는 게 맞다. PDU 는 ACC 만 끊으므로 B+ 가 살아 있고, 콜드부트가
    아니다. 조용히 ACC 사이클로 대체되면 검증하지 않은 것을 검증한
    것으로 집계하게 된다.
    """

    def __init__(self, client: PduClient) -> None:
        self.client = client

    def set(self, channel: int, on: bool) -> None:
        try:
            self.client.set_channel(channel, on)
        except PduError as exc:
            raise PduError(
                f"{exc}\n"
                f"  CH{channel} 로 왔습니다. POWER_CYCLE(콜드부트)은 PDU 로 "
                f"할 수 없습니다.\n"
                f"  ACC_CYCLE 로 배정하거나 PSU 출력 차단을 쓰세요."
            ) from exc

    def preflight(self, mapping=None, aliases=None) -> Dict[str, Any]:
        return self.client.preflight(mapping, aliases)

    def __repr__(self) -> str:
        return f"<PduRelay {self.client.base_url}>"


def build_controllers(
    client: PduClient,
    duts: Iterable[Mapping[str, Any]],
    channel_key: str = "pdu_channel",
    alias_key: str = "pdu_alias",
) -> Dict[str, PduController]:
    """duts.yaml 의 DUT 항목 리스트에서 컨트롤러를 만든다.

    pdu_channel 이 없는 DUT 는 건너뛴다 (전원 제어 없이 SOFT_CYCLE 만 씀).
    """
    out: Dict[str, PduController] = {}
    for entry in duts:
        ch = entry.get(channel_key)
        if ch is None:
            continue
        name = str(entry.get("name", f"ch{ch}"))
        out[name] = PduController(
            client, int(ch), expect_alias=entry.get(alias_key), name=name
        )
    return out


# --------------------------------------------------------------------------
# CLI  ―  python -m harness.pdu
# --------------------------------------------------------------------------

def _main(argv: Optional[List[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="PDU 상태 확인 / 수동 스위칭")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument(
        "--set",
        metavar="CH:STATE",
        help="채널 하나를 켜거나 끈다. 예) --set 8:off  (허용 채널만)",
    )
    ap.add_argument(
        "--cycle",
        metavar="CH:SEC",
        help="채널을 껐다가 SEC 초 뒤 다시 켠다. 예) --cycle 5:30",
    )
    args = ap.parse_args(argv)

    client = PduClient(base_url=args.url)

    try:
        if args.set:
            ch_s, _, state_s = args.set.partition(":")
            on = state_s.strip().lower() in ("on", "1", "true", "t")
            client.set_channel(int(ch_s), on)
            print(f"CH{int(ch_s)} -> {'ON' if on else 'OFF'}  (상태 확인 완료)")

        if args.cycle:
            ch_s, _, sec_s = args.cycle.partition(":")
            ch, sec = int(ch_s), float(sec_s)
            print(f"CH{ch} OFF ...")
            client.set_channel(ch, False)
            print(f"{sec}s 대기")
            time.sleep(sec)
            print(f"CH{ch} ON ...")
            client.set_channel(ch, True)
            print("완료. UART 로그에서 부팅 문자열을 확인하세요.")

        st = client.status()
        print(
            f"\nPDU  port={st.get('port')}  open={st.get('open')}  "
            f"connected={st.get('connected')}  addr={st.get('addr')}"
        )
        print(f"{'CH':>3}  {'상태':<5} {'허용':<5} 별칭")
        print("-" * 46)
        for n, info in sorted(client.channels().items()):
            allowed = "예" if n in client.allowed_channels else "-"
            print(
                f"{n:>3}  {'ON' if info['on'] else 'OFF':<5} "
                f"{allowed:<5} {info['alias']}"
            )
    except PduError as exc:
        print(f"\n[실패] {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
