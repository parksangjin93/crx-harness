"""
DUT 추상화.

테스트 코드는 Dut 인터페이스만 쓴다. Serial / 릴레이를 직접 부르지 않는다.
제품마다 다른 인터페이스는 구현체가 흡수한다.
"""

from __future__ import annotations

try:
    import msvcrt
except ModuleNotFoundError:
    msvcrt = None  # 오프라인 로그 재생에는 Windows 파일 잠금이 필요 없다.
import random
import re
import struct
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from .logsource import LogSource, SerialLogSource, SRC_RE

# --------------------------------------------------------------------------
# 리부팅 자극 스태거링
# --------------------------------------------------------------------------
#
# 2026-09-21 실측: DUT 4대가 물리적으로 CP2108 쿼드 브리지 하나를 공유한다
# (USB 링크 하나, 인터페이스 4개). 여러 대가 동시에 리부팅하면 부팅/종료
# 로그 burst 가 겹쳐 이 브리지의 포트별 FIFO 를 넘긴다 - Python 이나
# Windows 드라이버가 바이트를 보기도 전에 하드웨어 단에서 유실되므로
# flush 주기나 OS 수신 버퍼를 조정해도 안 고쳐졌다.
#
# 전체 사이클(약 80초)을 직렬화하면 4대 병렬 실행의 이점이 없어지므로,
# 리부팅 자극의 '시작 시각'만 프로세스 간에 최소 간격을 두고 스태거링
# 한다. burst 는 각 사이클 시작 후 정해진 시점(수 초~수십 초)에 나므로,
# 시작을 벌리면 burst 끼리 겹칠 확률이 낮아진다.
_ACTION_STAGGER_LOCK_PATH = Path(tempfile.gettempdir()) / "crx_harness_action_stagger.lock"
# 2026-09-21: 10s 로는 부족했다. 실측상 "시작 시각"만 벌려도 그 뒤로는
# 각 DUT 가 TC 마다 다른 소요시간으로 독립적으로 흘러가면서 시간이
# 지날수록 다시 겹친다 (vmc8pro 190초 창에서 나머지 3대가 계속 리부팅
# 중이었음). 간격을 늘려 겹칠 확률 자체를 더 낮춘다. 완전한 보장은
# 아니고, 확률을 줄이는 완화책이라는 점은 그대로다.
ACTION_STAGGER_MIN_GAP_S = 20.0
_STAGGERED_ACTIONS = frozenset({"POWER_CYCLE", "ACC_CYCLE", "SOFT_CYCLE", "HARD_REBOOT"})


def _stagger_action_start(min_gap_s: float = ACTION_STAGGER_MIN_GAP_S) -> None:
    """다른 DUT 프로세스의 마지막 리부팅 자극 시작 시각과 최소 간격을 둔다.

    프로세스 간에 공유해야 하므로 monotonic 이 아니라 wall clock(time.time())
    을 쓴다.

    2026-09-21 실측: 처음엔 락을 쥔 채로 필요한 만큼 sleep 했는데,
    min_gap_s 를 10s -> 20s 로 올리자마자 'OSError: Resource deadlock
    avoided' 로 TC 가 통째로 깨졌다. msvcrt.locking(LK_LOCK) 의 내장
    재시도는 최대 ~10초(1초 간격 10회)까지만 기다리고 포기하는데, 락을
    쥔 쪽이 20초씩 자고 있으니 뒤에 기다리는 프로세스가 그 한도를
    넘겨버린 것이다. 락 보유 시간을 sleep 에 결합시키면 안 된다.

    그래서 '표(ticket)' 방식으로 바꾼다: 락은 다음 시작 가능 시각을
    읽고-쓰는 짧은 순간에만 쥐고(수 ms), 실제 대기는 락을 놓은 뒤
    프로세스 자신의 sleep 으로 한다. 여러 프로세스가 동시에 와도 각자
    자기 차례(slot)를 예약만 하고 바로 풀어주므로 락 경합 시간이
    min_gap_s 와 무관하게 항상 짧다.

    'a+b' 모드는 write() 가 seek() 위치를 무시하고 항상 파일 끝에 붙는다
    (실제로 이 버그로 타임스탬프가 저장이 안 됐었다). 'r+b' 로 열어야
    seek 한 위치에 그대로 덮어써진다. 파일이 없거나 너무 작으면 먼저
    16바이트로 만들어 둔다.
    """
    if msvcrt is None:
        raise InfraError("실장비 재부팅 자극의 프로세스 간 잠금은 Windows에서만 지원합니다.")
    if not _ACTION_STAGGER_LOCK_PATH.exists() or \
            _ACTION_STAGGER_LOCK_PATH.stat().st_size < 16:
        with open(_ACTION_STAGGER_LOCK_PATH, "a+b") as init_fh:
            init_fh.seek(0, 2)
            if init_fh.tell() < 16:
                init_fh.write(b"\x00" * (16 - init_fh.tell()))

    with open(_ACTION_STAGGER_LOCK_PATH, "r+b") as fh:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        try:
            fh.seek(8)
            raw = fh.read(8)
            reserved_until = struct.unpack("d", raw)[0] if len(raw) == 8 else 0.0
            now = time.time()
            my_slot = max(now, reserved_until + min_gap_s)
            fh.seek(8)
            fh.write(struct.pack("d", my_slot))
            fh.flush()
        finally:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)

    wait = my_slot - time.time()
    if wait > 0:
        time.sleep(wait)


class DutTimeout(Exception):
    """기대한 이벤트가 시간 내에 발생하지 않음 -> FW 결함 후보"""


class InfraError(Exception):
    """릴레이/시리얼/케이블 등 장비 문제 -> 테스트 실패로 집계하지 않는다"""


@dataclass
class DutConfig:
    name: str = "crx1"
    product: str = "CRX"
    serial_port: str = "COM7"
    baudrate: int = 115200

    login_user: str = "root"
    login_password: str = ""          # 환경변수 DUT_PASSWORD 로 주입한다

    # 프롬프트
    login_prompt: str = r"login:|Password:"
    shell_prompt: str = r"^#|\n#"

    # 타이밍 (실측 기반)
    off_settle_s: float = 8.0         # 잔류 전하 방전. 짧으면 콜드부트가 아니다
    boot_done_pattern: str = r"\(LogWriter\) BOOT"
    boot_timeout_s: float = 300.0
    shutdown_pattern: str = r"\(LogWriter\) SHUTDOWN"
    shutdown_timeout_s: float = 90.0  # 에이징 실측 45.6s 의 2배
    default_timeout_s: float = 60.0

    app_process: str = "dvr_app"
    relay_channel: int = 0

    # ----- ACC_CYCLE (PDU 로 ACC 라인만 차단) -----
    # PDU 의 실제 채널 번호. relay_channel 에 오프셋을 더해 쓰지 말 것.
    pdu_channel: int = 0
    pdu_alias: str = ""               # power_status 별칭 대조용 (부분 일치)

    # 2026-09-10 CRX3212 실측 2회. 타임아웃은 실측의 2배 이상으로 잡았다.
    #   ACC 차단 -> ACC Remove          즉시
    #   ACC Remove -> SHUTDOWN          7.57 / 8.85s
    #   SHUTDOWN -> terminated          11.72 / 6.45s   편차 큼
    #   terminated -> Power down        3.61 / 3.68s
    #   ACC 인가 -> Loader Start        즉시
    #   Loader Start -> BOOT            44.2 / 43.5s
    #
    # acc_remove 는 자극이 먹었는지 보는 관문이다. 이게 안 나오면
    # 배선이 빠졌거나 채널이 틀린 것이므로 FW 실패가 아니라 InfraError 다.
    acc_remove_pattern: str = r"\(micom\) ACC Remove"
    acc_remove_timeout_s: float = 15.0
    app_exit_pattern: str = r"dvr_app is terminated"
    app_exit_timeout_s: float = 40.0
    power_down_pattern: str = r"reboot: Power down"
    # 2026-09-15 CRX3212/TRX3208 동시 실행 실측: 연달아 두 번째로 도는
    # ACC_CYCLE 은 'terminated' 이후 'reboot: Power down' 이 20s 를
    # 넘겨도 안 잡힐 때가 있었다 (실제로는 리부팅이 계속 진행됨).
    # 표본 2개(3.61/3.68s)로 잡은 20s 가 너무 타이트했던 것으로 보여
    # 40s 로 올린다.
    power_down_timeout_s: float = 40.0
    loader_pattern: str = r"Loader Start"
    loader_timeout_s: float = 30.0

    # 자극 치환. 릴레이가 없으면 POWER_CYCLE 을 SOFT_CYCLE 로 바꿔 돌린다.
    #   action_overrides: {POWER_CYCLE: SOFT_CYCLE}
    # 단 SOFT_CYCLE 은 MICOM 이 켜주는 소프트 사이클이라
    # 실제 배터리 분리와 다르다. 릴레이가 붙으면 되돌린다.
    action_overrides: dict = field(default_factory=dict)


class Dut(ABC):
    def __init__(self, cfg: DutConfig, source: LogSource):
        self.cfg = cfg
        self.source = source
        self._last_action_t: float = 0.0
        self.no_stimulus: bool = False
        # 자극 실행 중에 지나간 로그를 보관한다.
        # SOFT_CYCLE 은 종료 완료를 확인하려고 SHUTDOWN 을 기다리는데,
        # 그 줄이 큐에서 빠지면 TC003 이 같은 줄을 다시 찾지 못한다.
        self.action_buf: list = []
        self._recording: bool = False

    @property
    def last_action_t(self) -> float:
        """TIMEOUT_FROM=ACTION 의 기준 시각"""
        return self._last_action_t

    @abstractmethod
    def do_action(self, action: str) -> None: ...

    def mark_action_done(self) -> None:
        self._last_action_t = self.source.now()


class OfflineDut(Dut):
    """
    캡처된 로그로 판정식을 검증할 때 쓴다.
    자극은 실행하지 않고, 로그에서 해당 이벤트가 이미 일어난 지점을 찾는다.
    """

    # 자극별 앵커. 두 조건을 반드시 지켜야 한다.
    #   1. 자극이 "시작"되는 지점이어야 한다. 끝나는 지점을 잡으면
    #      그 사이에 나온 CHECK 대상을 이미 지나쳐버린다.
    #      예) SOFT_CYCLE 을 'dvr_app is terminated' 로 잡으면
    #          그보다 5초 앞선 SHUTDOWN 을 놓친다.
    #   2. 어떤 TC 의 CHECK 대상과도 겹치면 안 된다.
    #      예) POWER_CYCLE 을 'U-Boot 20' 으로 잡으면
    #          TC001 step1 이 검사할 줄을 앵커가 먼저 소비한다.
    #   ACC_CYCLE 은 규칙 2 를 아슬아슬하게 지킨다. 자극의 시작은
    #   '(micom) ACC Remove' 인데, TC005 가 같은 줄을 CHECK 로 쓰면
    #   앵커가 먼저 소비한다. TC005 판정식을 확인하고, 겹치면
    #   'MICOM_ACC_CHANGE' 나 카운트다운으로 옮길 것.
    #   (live 모드는 action_buf 가 보존하므로 영향 없다)
    ANCHORS = {
        "POWER_CYCLE": r"Loader Start",
        "ACC_CYCLE": r"\(micom\) ACC Remove",
        "SOFT_CYCLE": r"Record\[INT\] Status Change : \[Off\]",
        "HARD_REBOOT": r"Stopping DHCP server",
    }

    def do_action(self, action: str) -> None:
        pat = self.ANCHORS.get(action.split(":")[0])
        if pat is None:
            self.mark_action_done()
            return
        rx = re.compile(pat)
        while True:
            line = self.source.next_line(0)
            if line is None:
                raise DutTimeout(f"로그에서 자극 지점을 못 찾음: {action} ({pat})")
            if rx.search(line.text):
                self._last_action_t = line.t
                return


class UartDut(Dut):
    """COM 포트 단일 채널. adb 없음."""

    def __init__(self, cfg: DutConfig, source: SerialLogSource, relay=None):
        super().__init__(cfg, source)
        self.src: SerialLogSource = source
        self.relay = relay
        self._logged_in = False
        self._seen: list[str] = []

    # ---------- 로그인 ----------

    def ensure_login(self, timeout: float = 60) -> None:
        """
        로그인 상태를 확정한다.

        상태를 먼저 파악하고 그에 맞는 것만 보낸다.
        마커를 아무 때나 던지면 login 프롬프트에서 그게 계정명으로
        먹혀 Login incorrect 가 난다.

          IDLE  : Enter 만 눌러 상태를 떠본다
          LOGIN : 계정명 전송
          PWD   : 비밀번호 전송
          SHELL : 마커 전송 -> 응답이 오면 확정

        프롬프트 정규식으로 셸을 판정하지 않는다. 로그가 쏟아지는
        장비에서는 잘린 조각이 '#' 로 끝나 오인한다. 마커 응답만 믿는다.
        """
        login_rx = re.compile(r"\blogin:")
        pw_rx = re.compile(r"[Pp]assword:")
        prompt_rx = re.compile(r"[#$]\s*$")

        self.src.divert(True)
        self._seen = []
        state = "IDLE"
        tag = None
        attempts = 0
        try:
            deadline = time.monotonic() + timeout
            last_act = 0.0
            self.src.write("\r\n")

            while time.monotonic() < deadline:
                now = time.monotonic()
                line = self.src.next_cmd_line(0.3)

                if line is None:
                    if now - last_act > 3.0:
                        last_act = now
                        if state == "SHELL":
                            tag = f"{random.randint(100000, 999999)}"
                            self.src.write(f"echo PRB_{tag}_OK\r\n")
                        else:
                            self.src.write("\r\n")   # 상태를 떠본다
                    continue

                t = line.text
                self._seen.append(t)
                if len(self._seen) > 80:
                    del self._seen[:30]

                # 마커 응답이 오면 확정. 에코된 입력에는 'echo' 가 들어 있다.
                if tag and f"PRB_{tag}_OK" in t and "echo" not in t:
                    self._logged_in = True
                    return

                # 2026-09-17 실측: 카메라 인증정보 같은 앱 로그에
                # "username:admin password:admin" 처럼 'password:' 가
                # 우연히 들어 있어, 이걸 진짜 비밀번호 프롬프트로 오인해
                # 상태가 꼬이고 결국 '로그인 거부됨' 오탐으로 이어졌다
                # (crx3212 TC009). 출처 태그(dvr_app: 등)가 붙은 줄은
                # 콘솔 프롬프트가 아니라 앱이 찍은 로그이므로, 로그인
                # 판정에서는 아예 제외한다.
                if SRC_RE.match(t):
                    continue

                # PWD 상태(방금 비밀번호를 보낸 직후)가 아니면 우리 로그인
                # 시도와 무관한 문구다. 2026-09-29 crx3212 TC009 재발로 확인:
                # 카메라 인증 로그 줄이 인터리빙으로 태그 접두사와 분리돼
                # SRC_RE 필터를 피해가면서 'Login incorrect'만 단독으로
                # 찍히는 경우가 있었다 - 상태로 한 번 더 걸러낸다.
                if state == "PWD" and "Login incorrect" in t:
                    attempts += 1
                    if attempts >= 2:
                        raise InfraError(
                            f"로그인 거부됨 ({attempts}회). 계정 "
                            f"{self.cfg.login_user!r} 과 비밀번호를 확인하세요.\n"
                            "  DUT_PASSWORD 또는 config/secrets.yaml 값을\n"
                            "  실제 장비 비밀번호와 대조해보세요. 마지막 수신:\n"
                            + "\n".join(f"    | {x[:110]}" for x in self._seen[-8:]))
                    state = "IDLE"
                    tag = None
                    last_act = now
                    continue

                if pw_rx.search(t):
                    if not self.cfg.login_password:
                        raise InfraError(
                            "Password 프롬프트가 떴는데 비밀번호가 비어 있습니다.")
                    if state != "PWD" or now - last_act > 2:
                        state, last_act = "PWD", now
                        self.src.write(self.cfg.login_password + "\r\n")
                elif login_rx.search(t):
                    if state != "LOGIN" or now - last_act > 2:
                        state, last_act = "LOGIN", now
                        tag = None
                        self.src.write(self.cfg.login_user + "\r\n")
                elif state in ("IDLE", "PWD") and prompt_rx.search(t):
                    # 셸일 가능성. 로그가 쏟아지는 도중엔 이 한 줄만으로 단정
                    # 하지 않는다 - 2026-10-06 crx3212 TC039 재현: '#'/'$' 로
                    # 끝나는 로그 조각 바로 뒤에 'login:' 프롬프트가 이어지면서,
                    # 여기서 즉시 보낸 echo 마커가 그 로그인 프롬프트의 계정명
                    # 자리로 들어가 'Login incorrect' 를 유발했다 (echo 문자열
                    # 자체가 로그에 'CRX3212 login: echo PRB_xxx_OK' 로 찍힘).
                    # 상태만 SHELL 후보로 두고, 마커는 로그가 잠잠해진 뒤(아래
                    # line is None 분기)에만 보낸다. 그 사이 login:/Password: 가
                    # 오면 해당 분기가 먼저 걸려 LOGIN/PWD 로 바로 정정된다.
                    state, last_act = "SHELL", now
                    tag = None

            raise InfraError(
                f"셸 응답 없음 ({timeout:.0f}s, 상태 {state}).\n"
                "  TX 배선, 계정/비밀번호, 다른 프로그램의 포트 점유를\n"
                "  확인하세요. 마지막 수신:\n"
                + "\n".join(f"    | {x[:110]}" for x in self._seen[-8:]))
        finally:
            self.src.divert(False)
            self._absorb_divert()

    # ---------- 명령 ----------

    def shell(self, cmd: str, timeout: float = 30, check: bool = True) -> tuple[int, str]:
        """
        마커로 감싼다. 프롬프트 복귀로 완료를 판단하면 로그가 섞였을 때 오탐한다.
        매번 난수를 붙여야 이전 명령의 잔여 출력과 안 섞인다.
        """
        if not self._logged_in:
            self.ensure_login()

        tag = f"{random.randint(100000, 999999)}"
        start, end = f"__S_{tag}__", f"__E_{tag}__"

        self.src.divert(True)
        try:
            self.src.write(f"echo {start}; {cmd}; echo {end}$?\r\n")
            body: list[str] = []
            rc = -1
            deadline = time.monotonic() + timeout
            started = False
            while time.monotonic() < deadline:
                line = self.src.next_cmd_line(0.5)
                if line is None:
                    continue
                t = line.text
                if not started:
                    # 에코된 명령줄 자체는 건너뛴다 (echo __S_ 를 포함하므로)
                    if start in t and "echo" not in t:
                        started = True
                    continue
                m = re.search(rf"{end}(\d+)", t)
                if m:
                    rc = int(m.group(1))
                    break
                body.append(t)
            else:
                self._logged_in = False
                tail = "\n".join(f"    | {x[:110]}" for x in body[-5:]) or "    | (없음)"
                raise InfraError(
                    f"명령 응답 없음 ({timeout}s): {cmd}\n마지막 수신:\n{tail}")
        finally:
            self.src.divert(False)
            self._absorb_divert()

        out = "\n".join(body).strip()
        if check and rc != 0:
            raise AssertionError(f"명령 실패 (rc={rc}): {cmd}\n{out}")
        return rc, out

    # ---------- 자극 ----------

    def do_action(self, action: str) -> None:
        # 자극을 주기 직전에 큐를 비운다.
        # 안 비우면 이전 TC 가 남긴 줄을 이번 자극의 결과로 본다.
        drained = self.src.drain() if hasattr(self.src, "drain") else 0
        if drained:
            self.action_buf = []
        name, _, arg = action.partition(":")
        name = name.strip().upper()
        sub = (self.cfg.action_overrides or {}).get(name)
        if sub:
            name, _, arg = str(sub).partition(":")
            name = name.strip().upper()

        # 리부팅을 일으키는 자극만 스태거링한다. SHELL/WAIT 는 burst 를
        # 만들지 않으므로 그냥 바로 진행한다.
        if name in _STAGGERED_ACTIONS:
            _stagger_action_start()

        # 자극이 도는 동안 지나가는 줄을 action_buf 에 보관한다.
        # 자극은 완료 확인용으로 로그를 소비한다. ACC_CYCLE 은 Loader Start
        # 부터 (LogWriter) BOOT 까지 44초 분량을 먹는다. 그 안에 CHECK 대상이
        # 있으면 판정을 시작할 때는 이미 없다.
        #   TC004 System turned on by ACC ON
        #   TC006 ignition NET_IGNITION_INIT -> NET_IGNITION_ON
        #   TC013/014/015 RECORD OFF
        # 이 줄들이 여기 남아야 CHECK 가 다시 볼 수 있다.
        self._recording = True
        try:
            if name == "POWER_CYCLE":
                self.power_cycle()
            elif name == "ACC_CYCLE":
                self.acc_cycle()
            elif name == "SOFT_CYCLE":
                self.soft_cycle()
            elif name == "HARD_REBOOT":
                self.hard_reboot()
            elif name == "SHELL":
                self.shell(arg, check=False)
            elif name == "WAIT":
                time.sleep(float(arg))
            else:
                raise InfraError(f"알 수 없는 ACTION: {action}")
        finally:
            self._recording = False
        self.mark_action_done()

    def power_cycle(self) -> None:
        """릴레이 하드 전원 사이클. 진짜 콜드부트."""
        if self.relay is None:
            raise InfraError("릴레이가 구성되지 않음 (POWER_CYCLE 불가)")
        self.relay.set(self.cfg.relay_channel, False)
        if not self._wait_dead(15):
            raise InfraError("릴레이 off 했으나 로그가 계속 나옴 (접점 확인 필요)")
        time.sleep(self.cfg.off_settle_s)
        self.relay.set(self.cfg.relay_channel, True)
        self._logged_in = False

    def acc_cycle(self) -> None:
        """
        PDU 로 ACC 라인만 끊었다 다시 넣는다. 이그니션 사이클이다.

        B+ 는 살아 있으므로 콜드부트가 아니다. 대신 MICOM 이 전원 관리
        전체를 태우기 때문에 SOFT_CYCLE 과 종료 경로가 다르다.
        Save Odometer / hwclock -w / door unlock 은 이 경로에만 나온다.

        각 단계를 '기다린 뒤' 다음으로 간다. 안 기다리면 종료 중인 장비에
        전원을 다시 넣게 되고, 그 뒤로 하네스 인식과 장비 상태가 계속
        어긋난다. SOFT_CYCLE 이 재기동을 안 기다려서 무너졌던 것과 같다.

        전원을 내린 뒤로는 무슨 일이 있어도 다시 올린다. 중간에 실패한 채
        빠져나가면 DUT 가 꺼진 채로 남아 이후 TC 가 전부 죽는다.
        """
        if self.relay is None:
            raise InfraError("전원 컨트롤러가 구성되지 않음 (ACC_CYCLE 불가)")

        ch = self.cfg.pdu_channel
        self.relay.set(ch, False)
        restored = False
        success = False
        try:
            # 1. 자극이 먹었는지부터 확인한다.
            #    배선이 빠졌거나 채널이 틀리면 여기서 바로 잡힌다.
            #
            #    2026-09-17 실측: 이 한 줄도 'reboot: Power down' 과 같은
            #    간헐적 캡처 유실을 보인다. crx3212 에서 프로세스가 갓
            #    시작한 첫 사이클(배선 이력 전무)에도 재현되어 배선
            #    문제로 단정할 수 없다. 그렇다고 무조건 무시하면 진짜
            #    배선/채널 오류를 놓친다. 그래서 다음 신호(SHUTDOWN)로
            #    실제로 자극이 먹혔는지 교차 확인한다: SHUTDOWN 까지
            #    나오면 ACC Remove 하나 놓친 건 넘어가고, SHUTDOWN 마저
            #    안 나오면 그때는 진짜 배선 문제로 보고 InfraError 를 낸다.
            acc_remove_seen = True
            try:
                self.wait_log(self.cfg.acc_remove_pattern,
                              self.cfg.acc_remove_timeout_s)
            except DutTimeout:
                acc_remove_seen = False

            # 2. 종료 시퀀스. 여기서부터는 FW 동작이므로 DutTimeout 그대로 올린다.
            try:
                self.wait_log(self.cfg.shutdown_pattern, self.cfg.shutdown_timeout_s)
            except DutTimeout as e:
                if not acc_remove_seen:
                    raise InfraError(
                        f"ACC 를 끊었으나 '{self.cfg.acc_remove_pattern}' 도 "
                        f"'{self.cfg.shutdown_pattern}' 도 안 나왔습니다 (CH{ch}).\n"
                        "  배선 또는 pdu_channel 을 확인하세요.") from e
                raise
            if not acc_remove_seen:
                print(f"[{self.cfg.name}] 경고: "
                      f"'{self.cfg.acc_remove_pattern}' 미검출했지만 "
                      f"'{self.cfg.shutdown_pattern}' 은 확인됨 - 무시하고 계속 진행")
            self.wait_log(self.cfg.app_exit_pattern, self.cfg.app_exit_timeout_s)
            try:
                self.wait_log(self.cfg.power_down_pattern, self.cfg.power_down_timeout_s)
            except DutTimeout:
                # 2026-09-16 실측: 이 한 줄만 간헐적으로 캡처가 안 된다.
                # overrun 이 0건인 사이클(트랜잭션 부하 없는 첫 사이클 포함)
                # 에서도 빠지는 걸 여러 DUT 에서 반복 확인했다. 릴레이는
                # 이미 껐고(ACC Remove/SHUTDOWN/terminated 는 이미 확인됨),
                # 그 뒤로도 Loader Start 가 정상적으로 나오는 걸로 봐서
                # 실제 전원 차단은 매번 일어난다. 이 줄 하나가 안 잡혔다고
                # TC 전체를 FW 결함으로 잡지 않는다 (아래 loader_pattern 이
                # 진짜 재부팅 확인이다).
                print(f"[{self.cfg.name}] 경고: "
                      f"'{self.cfg.power_down_pattern}' 미검출 - 무시하고 계속 진행")
            self._logged_in = False

            # 3. 전원이 실제로 내려간 뒤 잠깐 둔다.
            time.sleep(self.cfg.off_settle_s)

            # 4. 재인가
            self.relay.set(ch, True)
            restored = True
            self.wait_log(self.cfg.loader_pattern, self.cfg.loader_timeout_s)
            try:
                self.wait_log(self.cfg.boot_done_pattern, self.cfg.boot_timeout_s)
            except DutTimeout:
                # 2026-10-07 vmc8pro 에서 3사이클 연속 재현 확인(cycle5/6/7,
                # TC017/TC010/TC113): boot_timeout_s(300s) 를 넘겨도 재부팅
                # 자체는 매번 정상 속도(Loader Start 로부터 45초 내외)로
                # 끝나고 다음 사이클까지 멀쩡히 진행됐다 - '(LogWriter) BOOT'
                # 로그 한 줄만 캡처가 안 된 것으로 확인됨(타임아웃을 더
                # 늘려도 로그 자체가 없어서 소용없다). ACC Remove/SHUTDOWN
                # 때와 같은 철학으로, 다음 단계 신호(셸 로그인 응답)로 실제
                # 부팅 여부를 교차 확인한다. 로그인에 성공하면 BOOT 로그만
                # 놓친 것으로 보고 넘어가고, 로그인마저 안 되면(ensure_login
                # 이 InfraError 를 던짐) 진짜 부팅 실패로 취급한다.
                self.ensure_login()
                print(f"[{self.cfg.name}] 경고: "
                      f"'{self.cfg.boot_done_pattern}' 미검출했지만 "
                      "로그인 셸 응답은 확인됨 - BOOT 로그 누락으로 보고 무시하고 계속 진행")
            else:
                self.ensure_login()
            success = True
        finally:
            if not success:
                if not restored:
                    # 실패로 빠져나가는 길에도 전원은 돌려놓는다.
                    try:
                        self.relay.set(ch, True)
                    except Exception:                        # noqa: BLE001
                        pass
                    self._logged_in = False

                # 2026-09-15 실측: 여기서 리부팅 완료를 안 기다리고 바로
                # 리턴하면, 다음 TC 가 곧바로 또 acc_cycle() 을 호출해
                # 방금 켠 지 1초도 안 된 전원을 다시 끊는다. 릴레이를
                # 그렇게 초 단위로 두 번 토글하면 MICOM 이 정상 엣지를
                # 못 보고 상태가 꼬여 이후 TC 가 연쇄로 InfraError 난다.
                #
                # 2026-09-22 실측: restored=True 이후(재인가 -> Loader
                # Start/BOOT/로그인 대기 중) 실패한 경우는 이 블록이
                # 아예 안 들어와서 위 문제가 그대로 재현됐다 (TC027 실패
                # 직후 TC028 이 곧바로 InfraError 로 연쇄). restored 값과
                # 무관하게 항상 최대한 리부팅이 끝나길 기다린다 - 이미
                # loader_pattern 까지 통과했다면 그 지점부터 재시도되는
                # 셈이라 낭비도 크지 않다.
                # 실패를 감추지 않도록 원래 예외는 그대로 전파한다.
                try:
                    self.wait_log(self.cfg.loader_pattern, self.cfg.loader_timeout_s)
                    self.wait_log(self.cfg.boot_done_pattern, self.cfg.boot_timeout_s)
                    self.ensure_login()
                except Exception:                            # noqa: BLE001
                    pass

    def soft_cycle(self) -> None:
        """
        dvr_app 을 정상 종료시키고 재기동까지 기다린다.

        /etc/init.d/driverec 이 앱 종료를 감지해 poweroff 를 걸고,
        MICOM 이 ACC 유지 상태를 보고 다시 켠다.

        부팅 완료까지 기다리는 것이 중요하다. 안 기다리면 다음 TC 가
        부팅 중에 killall 을 보내고, 죽일 프로세스가 없으니 busybox
        killall 은 0 을 반환한다. 그 상태로 SHUTDOWN 을 기다리면
        영영 안 나온다.
        """
        # 1. 앱이 실제로 떠 있는지 먼저 확인한다
        if not self._app_running(retry=3):
            raise InfraError(
                f"{self.cfg.app_process} 가 동작 중이 아닙니다.\n"
                "  앞선 TC 의 재기동이 아직 안 끝났을 수 있습니다.")

        # 2. 종료 요청
        self.shell(f"killall -TERM {self.cfg.app_process}", check=False)
        terminated = False
        try:
            self.wait_log(self.cfg.shutdown_pattern, self.cfg.shutdown_timeout_s)
            terminated = True
        except DutTimeout:
            # SHUTDOWN 로그 한 줄이 캡처에서 간헐적으로 누락되는 현상(CP2108 공유 이슈)이
            # 있어, 다음 로그(dvr_app is terminated)로 실제 종료 여부를 교차 확인한다.
            print(f"[WARN] {self.cfg.shutdown_pattern} 미검출 - "
                  "'dvr_app is terminated' 로 교차 확인 시도")
        if not terminated:
            try:
                self.wait_log(r"dvr_app is terminated", 30)
                terminated = True
            except DutTimeout:
                # 두 로그가 다 안 나왔다 - 로그 손실이 겹쳤을 수도, 진짜 응답 없음일
                # 수도 있다. 최종적으로 재부팅 완료(BOOT+로그인) 여부로 판정한다.
                print("[WARN] 'dvr_app is terminated' 도 미검출 - "
                      "재부팅 완료 여부로 최종 확인 시도")
        self._logged_in = False

        # 3. 재기동 완료까지 기다린다. 여기까지 해야 다음 TC 가 정상 상태에서 시작한다.
        #    terminated 가 False 라도(로그 두 줄 다 누락) 실제로 재부팅+로그인까지
        #    되면 정상으로 본다 - 로그 손실 vs 진짜 미종료를 여기서 최종적으로 가른다.
        try:
            try:
                self.wait_log(self.cfg.boot_done_pattern, self.cfg.boot_timeout_s)
            except DutTimeout:
                # 2026-10-13 xvm508/vmc8pro 실측: acc_cycle() 에서 먼저
                # 확인된 것과 동일하게, BOOT 로그 한 줄만 간헐적으로
                # 캡처가 안 되는 경우가 있다. ensure_login() 으로 실제
                # 셸 응답이 오는지 교차 확인해서, 되면 BOOT 로그 누락으로
                # 보고 넘어간다. 여기서도 로그인이 안 되면(즉 예외가
                # 나면) 아래 except 로 그대로 전파되어 진짜 실패로 처리된다.
                self.ensure_login()
                print(f"[{self.cfg.name}] 경고: "
                      f"'{self.cfg.boot_done_pattern}' 미검출했지만 "
                      "로그인 셸 응답은 확인됨 - BOOT 로그 누락으로 보고 무시하고 계속 진행")
                return
            self.ensure_login()
        except Exception as e:
            if not terminated:
                raise InfraError(
                    f"패턴 미검출 ({self.cfg.shutdown_timeout_s}s): {self.cfg.shutdown_pattern}\n"
                    "  killall 은 보냈으나 종료/재부팅 로그를 모두 못 받았습니다.") from e
            raise

    def _absorb_divert(self) -> None:
        """
        divert 구간에 지나간 장비 로그를 자극 버퍼로 옮긴다.
        마커와 에코는 제외한다. 그것까지 넣으면 내가 친 명령이
        CHECK 에 매칭되는 오탐이 난다.
        """
        if not self._recording:
            return
        for line in getattr(self.src, "divert_log", []):
            t = line.text
            if "__S_" in t or "__E_" in t or "PRB_" in t or "echo " in t:
                continue
            self.action_buf.append(line)
        if len(self.action_buf) > 4000:
            del self.action_buf[:1000]

    def _app_running(self, retry: int = 3) -> bool:
        """
        앱이 떠 있는지 확인한다. 재기동 중이면 잠시 뒤 다시 본다.
        ps 출력에 './dvr_app' 처럼 경로가 붙을 수 있으므로 부분 일치로 본다.
        """
        for i in range(max(1, retry)):
            try:
                _, out = self.shell("ps", timeout=20, check=False)
            except Exception:                                # noqa: BLE001
                out = ""
            if self.cfg.app_process in out:
                return True
            if i < retry - 1:
                time.sleep(10)
                self._logged_in = False
        return False

    def hard_reboot(self) -> None:
        """
        shell reboot. 앱 종료가 0.35초 만에 SIGKILL 로 잘린다.
        RECORD OFF 까지는 나오지만 UNMOUNT / SHUTDOWN 은 도달하지 않는다.
        """
        self.shell("sync", check=False)
        self.src.divert(True)
        try:
            self.src.write("reboot\r\n")
        finally:
            self.src.divert(False)
            self._absorb_divert()
        self._logged_in = False

    # ---------- 대기 ----------

    def wait_log(self, pattern: str, timeout: float) -> float:
        rx = re.compile(pattern)
        # 먼저 이미 쌓인 버퍼(바로 전 단계를 기다리는 동안 지나간 줄들)
        # 부터 훑는다. 2026-10-13 xvm508 실측으로 확인된 구조적 버그:
        # acc_cycle/soft_cycle 이 SHUTDOWN -> terminated -> BOOT 순서로
        # 이 메서드를 연달아 호출하는데, 예를 들어 'terminated' 를 기다리는
        # 30초 창 안에 BOOT 로그가 먼저 지나가버리면, 그 줄은 action_buf
        # 에 쌓이기만 하고 아래 while 루프(next_line) 는 그걸 다시 보지
        # 않는다 - 그러면 바로 다음에 오는 BOOT 대기가 "새로 들어오는
        # 줄"만 보다가 이미 지나간 BOOT 을 영영 못 찾고 타임아웃(300초)을
        # 전부 소진한다 (TC013 에서 그대로 재현: terminated 30초 창 안에
        # BOOT 이 찍혔는데 그 직후 BOOT 300초 대기가 실패함). verdict.py
        # 의 TC 판정 쪽(_await_one)은 action_buf 를 먼저 훑어서 이 문제가
        # 없는데, acc_cycle/soft_cycle 내부 전용인 이 메서드만 빠져 있었다.
        for line in list(getattr(self, "action_buf", [])):
            if rx.search(line.text):
                return line.t
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self.source.next_line(0.5)
            if not line:
                continue
            if self._recording:
                self.action_buf.append(line)
                if len(self.action_buf) > 4000:
                    del self.action_buf[:1000]
            if rx.search(line.text):
                return line.t
        raise DutTimeout(f"패턴 미검출 ({timeout}s): {pattern}")

    def wait_boot(self) -> float:
        return self.wait_log(self.cfg.boot_done_pattern, self.cfg.boot_timeout_s)

    def _wait_dead(self, timeout: float) -> bool:
        """로그가 조용해지면 죽은 것으로 본다."""
        deadline = time.monotonic() + timeout
        last = time.monotonic()
        while time.monotonic() < deadline:
            line = self.source.next_line(0.5)
            if line is not None:
                if self._recording:
                    self.action_buf.append(line)
                last = time.monotonic()
            elif time.monotonic() - last > 3:
                return True
        return False


class RelayController:
    """릴레이 컨트롤러 어댑터. 실제 장비 API 로 교체한다."""

    def __init__(self, port: str):
        self.port = port

    def set(self, channel: int, on: bool) -> None:
        raise NotImplementedError(
            "relay_controller 연동 필요. 채널 on/off 만 구현하면 된다.")
