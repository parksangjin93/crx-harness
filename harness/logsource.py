"""
로그 소스.

UART 는 로그와 명령이 같은 선을 쓴다. 그래서 원칙이 두 가지다.

  1. Serial 객체는 하나만 만든다. 같은 COM 포트를 두 번 열면
     Windows 에서는 실패하고, 열리더라도 바이트가 랜덤하게 나뉜다.
  2. 리더 스레드도 하나만 둔다. 그 스레드가 모든 줄을 읽어
     판정 큐와 명령 응답 큐로 나눠 보낸다.

명령 실행 중 오가는 줄(에코 포함)은 판정 큐로 보내지 않는다.
안 그러면 내가 친 명령 문자열이 CHECK 에 매칭되는 오탐이 난다.
"""

from __future__ import annotations

import queue
import re
import socket
import threading
import time
from datetime import datetime
from abc import ABC, abstractmethod
from pathlib import Path

# 기존 캡처 툴 형식: [2026-09-01-13시17분49.871초]본문
TS_RE = re.compile(r"^\[(\d{4})-(\d{2})-(\d{2})-(\d{2})시(\d{2})분([\d.]+)초\]")

# 하네스가 쓰는 형식: 09:58:10.346 |    1.2 | dvr_app | 본문
HARNESS_RE = re.compile(
    r"^(\d{2}):(\d{2}):(\d{2})\.(\d{3})\s*\|\s*([\d.-]+)\s*\|\s*(\S+)\s*\|\s?(.*)$")

# 부팅 기준점. 이 문자열이 보이면 경과 시간을 0 으로 되돌린다.
BOOT_MARK_RE = re.compile(r"U-Boot 20|Loader Start")

# 본문 앞에 붙는 출처 태그: "dvr_app: ", "dvr: ", "dms4: "
# 소문자로 시작하는 것만 인정한다. 안 그러면 "Password: " 나
# "Stopping bluetoothd: " 같은 본문이 출처로 잘못 잡힌다.
SRC_RE = re.compile(r"^([a-z][a-z0-9_.-]{0,15}):\s")


def split_source(text: str) -> tuple[str, str]:
    """본문에서 출처 태그를 떼어낸다. 없으면 '---'."""
    m = SRC_RE.match(text)
    if m:
        return m.group(1), text[m.end():]
    return "---", text


def format_line(wall: float, since_boot: float, text: str) -> str:
    """
    시각 | 경과 | 출처 | 본문

    경과는 마지막 부팅 기준 초. (LogWriter) BOOT 가 45초에 떴는지
    60초에 떴는지 뺄셈 없이 바로 보인다.
    """
    src, body = split_source(text)
    hhmmss = datetime.fromtimestamp(wall).strftime("%H:%M:%S.") + \
        f"{int(wall * 1000) % 1000:03d}"
    return f"{hhmmss} | {since_boot:7.1f} | {src:<8.8s} | {body}"


# 줄바꿈 없이 이 시간 이상 멈춰 있으면 버퍼를 한 줄로 내보낸다.
# 115200 에서 한 줄이 이보다 오래 걸릴 일은 없다.
PARTIAL_FLUSH_S = 0.15


class LogLine:
    __slots__ = ("t", "text", "raw", "partial")

    def __init__(self, t: float, text: str, raw: str):
        self.partial = False
        self.t = t          # 단조 시각(초). 판정의 유일한 기준
        self.text = text    # 타임스탬프 제거한 본문
        self.raw = raw

    def __repr__(self) -> str:
        return f"<{self.t:.3f} {self.text[:60]}>"


class LogSource(ABC):
    """실시간(UART)과 오프라인(파일)이 같은 인터페이스를 쓴다."""

    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def next_line(self, timeout: float) -> LogLine | None: ...

    @abstractmethod
    def now(self) -> float: ...


# 2026-09-16 실측: 매 줄마다 flush() 하면, 부팅 직후 ADAS/BSD 모델 로딩처럼
# 같은 1ms 안에 수십 줄이 몰아치는 구간에서 리더 스레드가 디스크 flush 를
# 기다리느라 UART 를 못 읽어 OS 수신 버퍼가 넘친다. 그 결과 'reboot: Power
# down', 'update disk info SD:' 같은 줄이 그 부팅 사이클에서만 통째로
# 사라지는 걸 여러 DUT/여러 메시지에서 반복 확인했다. 매 줄 flush 대신
# 시간 간격으로만 flush 해 리더 스레드가 burst 구간에서 막히지 않게 한다.
FLUSH_INTERVAL_S = 0.1


class RotatingLog:
    """
    UART 로그 파일. 실행 시각으로 이름을 짓고, 커지면 순번을 올린다.

        2026-09-08-14시23분05초.log        실행 시작
        2026-09-08-14시23분05초.002.log    max_mb 를 넘어 넘어간 것

    크기로 통째로 잘라내지 않고 회전시키는 이유는, 결함이 난 순간의
    로그가 잘려나가면 안 되기 때문이다. 오래된 파일부터 지운다.
    """

    def __init__(self, log_dir: Path, max_mb: int = 200, keep: int = 20,
                 stem: str | None = None):
        self.dir = Path(log_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max(1, max_mb) * 1024 * 1024
        self.keep = max(1, keep)
        # 실행 폴더 이름이 이미 시각이므로 파일명은 uart 로 고정한다.
        self.stem = stem or datetime.now().strftime("%Y-%m-%d-%H시%M분%S초")
        self.seq = 1
        self.written = 0
        self.path = self.dir / f"{self.stem}.log"
        self.fh = self.path.open("w", encoding="utf-8", errors="replace")
        self._last_flush = time.monotonic()
        self._link_latest()
        self._prune()

    def _link_latest(self) -> None:
        """모니터와 scan 이 항상 같은 이름으로 최신 파일을 찾게 한다."""
        try:
            (self.dir / "latest.txt").write_text(self.path.name, encoding="utf-8")
        except OSError:
            pass

    def write(self, text: str) -> None:
        data = text + "\n"
        self.fh.write(data)
        now = time.monotonic()
        if now - self._last_flush >= FLUSH_INTERVAL_S:
            self.fh.flush()      # 크래시가 나도 로그가 남게 주기적으로만 flush
            self._last_flush = now
        self.written += len(data.encode("utf-8", errors="replace"))
        if self.written >= self.max_bytes:
            self._roll()

    def _roll(self) -> None:
        self.fh.close()
        self.seq += 1
        self.path = self.dir / f"{self.stem}.{self.seq:03d}.log"
        self.fh = self.path.open("w", encoding="utf-8", errors="replace")
        self._last_flush = time.monotonic()
        self.written = 0
        self._link_latest()
        self._prune()

    def _prune(self) -> None:
        files = sorted(self.dir.glob("*.log"), key=lambda p: p.stat().st_mtime)
        for old in files[:-self.keep]:
            try:
                old.unlink()
            except OSError:
                pass

    def close(self) -> None:
        try:
            self.fh.close()
        except OSError:
            pass


class SerialLogSource(LogSource):
    """실시간 UART. Serial 객체와 리더 스레드를 각각 하나만 소유한다."""

    def __init__(self, port: str, baudrate: int = 115200,
                 artifact: Path | None = None,
                 log_dir: Path | None = None,
                 max_mb: int = 200, keep: int = 20,
                 stem: str | None = None):
        self.port = port
        self.baudrate = baudrate
        self.artifact = artifact
        self.log_dir = log_dir
        self.max_mb = max_mb
        self.keep = keep
        self.stem = stem
        self.rot: RotatingLog | None = None
        self._boot_t: float = 0.0
        self._ser = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._verdict_q: queue.Queue[LogLine] = queue.Queue()
        self._cmd_q: queue.Queue[LogLine] = queue.Queue()
        self._divert = threading.Event()    # set 이면 명령 응답 큐로 보낸다
        self.divert_log: list = []          # divert 구간에 지나간 줄
        self._fh = None
        self._lock = threading.Lock()

    def start(self) -> None:
        import serial  # pyserial

        self._ser = serial.Serial(self.port, self.baudrate, timeout=0.2)
        # 2026-09-16: pyserial(Windows) 의 기본 수신 버퍼는 4096B 다.
        # DUT 4대를 프로세스로 동시에 돌리면 부팅 직후 몰아치는 로그
        # burst 구간에서 리더 스레드가 OS 스케줄링에 밀려 잠깐 못
        # 읽는 순간, 4096B 는 수십 줄만 돼도 넘친다. 그 결과가
        # 'reboot: Power down' / '(LogWriter) BOOT' 같은 줄이 특정
        # 부팅 사이클에서만 사라지는 현상으로 보인다. flush 주기를
        # 줄인 것만으론 부족했으니 OS 버퍼 자체를 넉넉히 키운다.
        try:
            self._ser.set_buffer_size(rx_size=131072, tx_size=4096)
        except AttributeError:
            pass    # Windows 전용 확장. 다른 OS 에서는 조용히 건너뛴다.
        if self.log_dir:
            self.rot = RotatingLog(self.log_dir, self.max_mb, self.keep, self.stem)
            self.artifact = self.rot.path
        elif self.artifact:
            self.artifact.parent.mkdir(parents=True, exist_ok=True)
            self._fh = self.artifact.open("w", encoding="utf-8", errors="replace")

        def run() -> None:
            buf = b""
            last = time.monotonic()
            while not self._stop.is_set():
                try:
                    # OS 버퍼(131072B)를 한 번에 최대한 비워 밀린 만큼
                    # 라운드트립을 줄인다. read() 는 그만큼 안 쌓여
                    # 있으면 있는 만큼만 반환하니 과도하게 기다리지 않는다.
                    chunk = self._ser.read(65536)
                except Exception as e:                       # noqa: BLE001
                    self._verdict_q.put(LogLine(time.monotonic(),
                                                f"!!! serial reader died: {e}", ""))
                    return
                if chunk:
                    buf += chunk
                    while b"\n" in buf:
                        raw, buf = buf.split(b"\n", 1)
                        self._emit(raw.decode("utf-8", errors="replace").rstrip("\r"))
                    last = time.monotonic()
                elif buf and time.monotonic() - last > PARTIAL_FLUSH_S:
                    # 줄바꿈 없이 멈춘 버퍼를 내보낸다.
                    # 셸 프롬프트("# ")와 로그인 프롬프트("login: ")는
                    # 입력을 기다리는 상태라 \n 이 안 붙는다.
                    # 이게 없으면 프롬프트가 영영 안 보인다.
                    self._emit(buf.decode("utf-8", errors="replace").rstrip("\r"),
                               partial=True)
                    buf = b""

        self._thread = threading.Thread(target=run, daemon=True, name="uart-reader")
        self._thread.start()

    def _emit(self, text: str, partial: bool = False) -> None:
        line = LogLine(time.monotonic(), text, text)
        line.partial = partial
        if BOOT_MARK_RE.search(text):
            self._boot_t = line.t          # 부팅 감지 -> 경과 시간 리셋
        rec = format_line(time.time(), line.t - self._boot_t, text)
        if self.rot:
            self.rot.write(rec)
            self.artifact = self.rot.path      # 회전하면 최신 경로로 갱신
        elif self._fh:
            self._fh.write(rec + "\n")
            self._fh.flush()    # 크래시가 나도 로그는 남아야 한다
        if self._divert.is_set():
            self._cmd_q.put(line)
            # divert 구간의 줄도 따로 남긴다. 명령 실행 중에 나온 장비 로그가
            # 판정에서 통째로 사라지면 안 된다 (BOOT 직후 FW_VERSION 등).
            self.divert_log.append(line)
        else:
            self._verdict_q.put(line)

    def write(self, data: str) -> None:
        with self._lock:
            self._ser.write(data.encode())
            self._ser.flush()

    def divert(self, on: bool) -> None:
        """명령 실행 구간에만 켠다. 그 사이 줄은 판정에서 제외된다."""
        if on:
            while not self._cmd_q.empty():
                self._cmd_q.get_nowait()
            self.divert_log = []
            self._divert.set()
        else:
            self._divert.clear()

    def next_cmd_line(self, timeout: float) -> LogLine | None:
        try:
            return self._cmd_q.get(timeout=timeout)
        except queue.Empty:
            return None

    def next_line(self, timeout: float) -> LogLine | None:
        try:
            return self._verdict_q.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain(self) -> int:
        """
        쌓여 있던 로그를 버린다. 자극을 주기 직전에 호출한다.
        안 비우면 이전 TC 가 남긴 줄을 이번 자극의 결과로 오판한다.
        """
        n = 0
        while not self._verdict_q.empty():
            self._verdict_q.get_nowait()
            n += 1
        return n

    def now(self) -> float:
        return time.monotonic()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        if self._ser:
            self._ser.close()
        if self.rot:
            self.rot.close()
        if self._fh:
            self._fh.close()


class DaemonUnavailable(Exception):
    """상시 프로그램(crx-continuous)에 접속할 수 없음 - 안 떠 있거나,
    다른 접속(사람의 수동 입력 등)이 이미 쓰고 있음. conftest.py 가 이걸
    받아서 그 DUT 를 skip 처리한다."""


class DaemonLogSource(LogSource):
    """
    상시 포트 프로그램(crx-continuous/continuous_log.py)에 접속해서 쓰는 버전.

    2026-10 "상시 포트 프로그램" 작업 4단계. 포트를 직접 열지 않는다.

      읽기: continuous_log.py 가 쓰고 있는 연속 로그 파일(continuous/<날짜>/
            uart.log)을 tail 한다.
      쓰기: continuous_log.py 의 입력 소켓(127.0.0.1:17000+pdu_channel)에
            접속해서 "이거 써줘"라고 부탁한다. 그 소켓은 한 번에 1접속만
            허용하므로, 사람이 수동 모드로 이미 붙어 있으면 접속 자체가
            거절된다(DaemonUnavailable) - conftest.py 가 이걸 pytest.skip
            으로 바꾼다. 이게 그대로 "수동 테스트 중인 기기는 건너뜀" 규칙의
            실제 구현이다 - 잠금 파일을 따로 또 확인할 필요가 없다.

    SerialLogSource 와 같은 인터페이스(write/divert/next_cmd_line/
    next_line/now/drain)를 제공하므로 UartDut 쪽은 전혀 손대지 않아도 된다.

    아직 안 되는 것: 연속 로그는 날짜 폴더 하나에 계속 쌓이고, 하네스의
    "실행 하나 = 폴더 하나" 산출물에는 그 구간만 잘라낸 uart.log 사본이
    run_dir 를 주면 접속 ~ 종료 구간만큼 그 폴더에도 uart.log 사본을
    같이 남긴다 - "실행 하나 = 폴더 하나" 구조를 맞추고, monitor.py 의
    실시간 로그 패널도 이걸 보고 그대로 동작한다.
    """

    CONTROL_PORT_BASE = 17000

    def __init__(self, base_log_dir: Path, pdu_channel: int,
                 run_dir: Path | None = None, poll_interval: float = 0.2):
        self.base_log_dir = Path(base_log_dir)
        self.control_port = self.CONTROL_PORT_BASE + int(pdu_channel)
        self.run_dir = Path(run_dir) if run_dir else None
        self._run_fh = None
        self.poll_interval = poll_interval
        self._sock: socket.socket | None = None
        self._path: Path | None = None
        self._pos = 0
        self._verdict_q: queue.Queue[LogLine] = queue.Queue()
        self._cmd_q: queue.Queue[LogLine] = queue.Queue()
        self._divert = threading.Event()
        self.divert_log: list = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._write_lock = threading.Lock()

    @property
    def artifact(self) -> Path | None:
        """
        지금 tail 하고 있는 연속 로그 파일 경로. test_spec.py 의
        check_forbidden (금지 패턴 검사, 모든 TC 에 autouse) 이
        dut.source.artifact 로 "이 TC 구간"을 가늠하는 데 쓴다 -
        이게 없으면 daemon 모드에서 그 검사가 조용히 무력화된다.
        """
        return self._path

    # ---------- 연결 ----------

    def start(self) -> None:
        try:
            sock = socket.create_connection(("127.0.0.1", self.control_port), timeout=10)
        except OSError as e:
            raise DaemonUnavailable(
                f"상시 프로그램에 접속 못함 (127.0.0.1:{self.control_port}): {e}\n"
                "  continuous_log.py 가 이 DUT 으로 돌고 있는지 확인하세요.") from e
        greeting = sock.recv(4096).decode("utf-8", errors="replace")
        if greeting.startswith("BUSY"):
            sock.close()
            raise DaemonUnavailable(
                f"다른 접속이 입력 중입니다: {greeting.strip()}\n"
                "  사람이 수동 모드(입력 접속)로 붙어 있어 자동 실행을 건너뜁니다.")
        self._sock = sock
        if self.run_dir is not None:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            self._run_fh = (self.run_dir / "uart.log").open(
                "a", encoding="utf-8", errors="replace", newline="\n")
        self._thread = threading.Thread(target=self._tail_loop, daemon=True,
                                        name="daemon-tail")
        self._thread.start()

    def _today_dir(self) -> Path:
        return self.base_log_dir / "continuous" / datetime.now().strftime("%Y-%m-%d")

    def _resolve_latest(self, dir_: Path) -> Path | None:
        marker = dir_ / "latest.txt"
        if marker.exists():
            p = dir_ / marker.read_text(encoding="utf-8").strip()
            if p.exists():
                return p
        return None

    def _current_log_path(self) -> Path | None:
        """
        오늘 폴더를 우선 찾는다. 자정을 넘겨도 continuous_log.py 는 재시작
        전까지 어제 폴더에 계속 쓰므로, 오늘 폴더가 없으면 continuous/ 밑에서
        가장 최근에 바뀐 폴더를 대신 찾는다.
        """
        p = self._resolve_latest(self._today_dir())
        if p is not None:
            return p
        cont = self.base_log_dir / "continuous"
        if not cont.is_dir():
            return None
        subs = [d for d in cont.iterdir() if d.is_dir()]
        if not subs:
            return None
        latest_dir = max(subs, key=lambda d: d.stat().st_mtime)
        return self._resolve_latest(latest_dir)

    def _tail_loop(self) -> None:
        while not self._stop.is_set():
            path = self._current_log_path()
            if path is None:
                time.sleep(self.poll_interval)
                continue
            if path != self._path:
                self._path = path
                self._pos = path.stat().st_size      # 접속 시점 이후 줄만 본다
                time.sleep(self.poll_interval)
                continue
            try:
                size = path.stat().st_size
            except OSError:
                time.sleep(self.poll_interval)
                continue
            if size < self._pos:
                self._pos = 0       # 회전/재시작으로 파일이 다시 작아짐
            if size > self._pos:
                # newline="\n" 로 고정해야 한다. 기본(universal newlines)
                # 모드는 \r 단독도 줄바꿈으로 친다 - 장비가 프롬프트를 다시
                # 그리려고 보내는 \r 이 줄 중간에 섞여 있으면, 그 \r 에서
                # 줄이 쪼개져서 뒷부분("login:" 같은 프롬프트 본문)이
                # 시각 접두사를 잃고 HARNESS_RE 에 안 걸려 조용히 버려진다
                # (실측: trx3208 에서 ensure_login() 이 "login:" 을 계속
                # 놓침). \n 만 줄바꿈으로 인정해야 이 문제가 없다.
                with path.open(encoding="utf-8", errors="replace", newline="\n") as f:
                    f.seek(self._pos)
                    # f.tell() 을 for 루프 "안"에서 부르면
                    # "OSError: telling position disabled by next() call" 가 난다
                    # (텍스트 모드 반복자의 내부 readahead 버퍼 때문). 루프가
                    # 끝난 뒤 한 번만 불러야 한다.
                    for raw in f:
                        self._emit(raw.rstrip("\n"))
                    self._pos = f.tell()
            time.sleep(self.poll_interval)

    def _emit(self, rec: str) -> None:
        if self._run_fh is not None:
            # 정규식 매치 여부와 무관하게 받은 그대로 남긴다 - 실행 폴더의
            # uart.log 는 연속 로그의 "이 구간" 사본이라, 판정에 안 쓰는
            # 줄이라도 빠지면 나중에 사람이 보는 로그가 잘려 보인다.
            self._run_fh.write(rec + "\n")
            self._run_fh.flush()
        m = HARNESS_RE.match(rec)
        if not m:
            return
        _hh, _mm, _ss, _ms, _elapsed, src, body = m.groups()
        text = body if src == "---" else f"{src}: {body}"
        line = LogLine(time.monotonic(), text, rec)
        if self._divert.is_set():
            self._cmd_q.put(line)
            self.divert_log.append(line)
        else:
            self._verdict_q.put(line)

    # ---------- SerialLogSource 와 같은 인터페이스 ----------

    def write(self, data: str) -> None:
        if self._sock is None:
            raise DaemonUnavailable("상시 프로그램 접속이 끊어졌습니다")
        with self._write_lock:
            try:
                self._sock.sendall(data.encode("utf-8"))
            except OSError as e:
                raise DaemonUnavailable(f"상시 프로그램에 쓰기 실패: {e}") from e

    def divert(self, on: bool) -> None:
        if on:
            while not self._cmd_q.empty():
                self._cmd_q.get_nowait()
            self.divert_log = []
            self._divert.set()
        else:
            self._divert.clear()

    def next_cmd_line(self, timeout: float) -> LogLine | None:
        try:
            return self._cmd_q.get(timeout=timeout)
        except queue.Empty:
            return None

    def next_line(self, timeout: float) -> LogLine | None:
        try:
            return self._verdict_q.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain(self) -> int:
        n = 0
        while not self._verdict_q.empty():
            self._verdict_q.get_nowait()
            n += 1
        return n

    def now(self) -> float:
        return time.monotonic()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._run_fh:
            try:
                self._run_fh.close()
            except OSError:
                pass


class FileLogSource(LogSource):
    """
    오프라인 재생. 캡처된 로그로 판정식을 먼저 검증할 때 쓴다.

    타임스탬프를 파싱해 상대 시각으로 바꾸므로 TIMEOUT 판정이
    실시간과 동일하게 동작한다. 실제로 기다리지는 않는다.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lines: list[LogLine] = []
        self._i = 0
        self._day = 0

    def start(self) -> None:
        t0 = None
        prev = None
        self._day = 0
        out: list[LogLine] = []
        # newline="\n" 로 고정한다. 기본(universal newlines) 모드는 장비가
        # 프롬프트를 다시 그리려고 보내는 \r 단독도 줄바꿈으로 쳐서, 그 \r
        # 에서 줄이 쪼개지며 뒷부분(login:/Password:/셸 프롬프트/마커 에코
        # 본문)이 시각 접두사를 잃어버린다 (2026-10 daemon 모드 실측으로
        # 발견 - 실제 캡처 로그에도 단독 \r 이 수백 개씩 있었다).
        with self.path.open(encoding="utf-8", errors="replace", newline="\n") as f:
            for raw in f:
                raw = raw.rstrip("\r\n").lstrip("\ufeff")
                m = TS_RE.match(raw)
                if m:
                    y, mo, d, hh, mm, ss = m.groups()
                    # 날짜가 바뀌어도 단조 증가하도록 epoch 로 환산
                    import datetime as dt
                    base = dt.datetime(int(y), int(mo), int(d), int(hh), int(mm))
                    t = base.timestamp() + float(ss)
                    text = raw[m.end():]
                else:
                    h2 = HARNESS_RE.match(raw)
                    if h2:
                        # 하네스가 쓴 로그. 자정을 넘기면 시:분:초가 되돌아가므로
                        # 되돌아간 순간 하루를 더해 단조 증가를 유지한다.
                        hh, mm, ss, ms, _elapsed, src, body = h2.groups()
                        t = (int(hh) * 3600 + int(mm) * 60 + int(ss)
                             + int(ms) / 1000.0) + self._day * 86400
                        if prev is not None and t < prev - 3600:
                            self._day += 1
                            t += 86400
                        prev = t
                        text = body if src == "---" else f"{src}: {body}"
                    else:
                        t = t0 if t0 is not None else 0.0
                        text = raw
                if t0 is None:
                    t0 = t
                out.append(LogLine(t - t0, text, raw))
        self._lines = out
        self._i = 0

    def next_line(self, timeout: float) -> LogLine | None:
        # timeout 은 무시한다. 시간 판정은 LogLine.t 로 한다.
        if self._i >= len(self._lines):
            return None
        line = self._lines[self._i]
        self._i += 1
        return line

    def now(self) -> float:
        if not self._lines:
            return 0.0
        i = min(self._i, len(self._lines) - 1)
        return self._lines[i].t

    def seek_start(self) -> None:
        self._i = 0

    def stop(self) -> None:
        pass
