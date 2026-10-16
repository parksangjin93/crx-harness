"""
라이브 연결 점검.

    python -m harness.check --dut xvm508
    python -m harness.check --all

테스트를 돌리기 전에 이것부터 통과시킨다.
포트가 안 열리는지, 로그가 안 흐르는지, 로그인이 안 되는지를
따로 구분해준다. 이게 없으면 테스트가 실패했을 때
연결 문제인지 판정 문제인지 알 수 없다.
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import yaml

from .dut import DutConfig, UartDut
from .logsource import SerialLogSource
from .secrets import HOWTO, dut_password

ROOT = Path(__file__).parent.parent

OK, NG, WARN = "[ OK ]", "[ NG ]", "[ ?? ]"


def load_entries(path: Path, only: str | None) -> list[dict]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if "duts" not in raw:
        return [raw]
    defaults = raw.get("defaults") or {}
    out = [{**defaults, **d} for d in raw["duts"]]
    if only:
        out = [d for d in out if d["name"] == only]
    return out


def _raw_dump(src, cfg, seconds: float = 6.0) -> None:
    """
    Enter 를 눌러보고 실제로 무엇이 오는지 그대로 보여준다.
    프롬프트 문자열이 예상과 다른지, 아예 아무 응답이 없는지 구분된다.
    """
    print(f"\n     --- Enter 입력 후 {seconds:.0f}초간 수신 원문 ---")
    src.divert(True)
    try:
        src.write("\r\n")
        end = time.monotonic() + seconds
        n = 0
        while time.monotonic() < end:
            line = src.next_cmd_line(0.4)
            if line is None:
                continue
            n += 1
            tag = "P" if getattr(line, "partial", False) else " "
            print(f"     {tag}| {line.text[:130]!r}")
            if n >= 25:
                break
        if n == 0:
            print("      | (아무것도 안 옴)")
            print("      Enter 에 응답이 없습니다. TX 배선이나 장비 상태를 확인하세요.")
    finally:
        src.divert(False)
    print("     --- 끝 ('P' 는 줄바꿈 없이 온 프롬프트 후보) ---")


def check_one(entry: dict, listen_s: float = 8.0, try_login: bool = True) -> bool:
    name = entry.get("name", "dut")
    print(f"\n{'=' * 62}\n{name}  ({entry.get('product', '?')})\n{'=' * 62}")

    # 1. 포트 찾기
    port = entry.get("serial_port")
    if entry.get("port_match"):
        try:
            from .ports import resolve
            port = resolve(entry["port_match"], port)
            print(f"{OK} 포트 확인: {port}")
        except Exception as e:                                # noqa: BLE001
            print(f"{NG} 포트 확인 실패\n     {e}")
            return False
    else:
        print(f"{WARN} port_match 없음 — serial_port 값을 그대로 씁니다: {port}")
    if not port:
        print(f"{NG} serial_port 가 비어 있습니다. duts.yaml 을 확인하세요.")
        return False

    # 2. 포트 열기
    raw = {**entry, "serial_port": port,
           "login_password": dut_password(name)}
    known = {f.name for f in DutConfig.__dataclass_fields__.values()}
    cfg = DutConfig(**{k: v for k, v in raw.items() if k in known})

    src = SerialLogSource(cfg.serial_port, cfg.baudrate)
    try:
        src.start()
        print(f"{OK} 포트 열림: {port} @ {cfg.baudrate}")
    except Exception as e:                                    # noqa: BLE001
        msg = str(e)
        print(f"{NG} 포트를 열 수 없습니다\n     {msg}")
        if "PermissionError" in msg or "액세스" in msg or "denied" in msg.lower():
            print("     다른 프로그램이 포트를 잡고 있습니다.")
            print("     SecureCRT / TeraTerm 세션을 닫거나 Disconnect 하세요.")
            print("     Windows 에서 COM 포트는 한 프로그램만 열 수 있습니다.")
        return False

    ok = True
    try:
        # 3. 로그가 흐르는지
        print(f"     로그 수신 확인 중... ({listen_s:.0f}초)")
        got, sample = 0, []
        end = time.monotonic() + listen_s
        while time.monotonic() < end:
            line = src.next_line(0.5)
            if line:
                got += 1
                if len(sample) < 3:
                    sample.append(line.text[:110])
        if got:
            print(f"{OK} 로그 수신: {got}줄 / {listen_s:.0f}초")
            for s in sample:
                print(f"       | {s}")
        else:
            print(f"{WARN} {listen_s:.0f}초간 로그가 없습니다")
            print("       장비가 꺼져 있거나, baudrate 가 다르거나, TX/RX 배선 문제입니다.")
            print("       shell 프롬프트만 뜨는 유휴 상태일 수도 있습니다.")

        # 4. 로그인 / 프롬프트
        if try_login:
            dut = UartDut(cfg, src, relay=None)
            try:
                dut.ensure_login(timeout=40)
                print(f"{OK} shell 프롬프트 확인")
            except Exception as e:                            # noqa: BLE001
                print(f"{NG} 로그인 실패\n     {e}")
                if not cfg.login_password:
                    print("     DUT_PASSWORD 환경변수가 비어 있습니다.")
                    print("       PowerShell:  $env:DUT_PASSWORD = '비밀번호'")
                _raw_dump(src, cfg)
                ok = False
            else:
                # 5. 명령 실행
                try:
                    rc, out = dut.shell("echo HARNESS_OK", timeout=15, check=False)
                    if "HARNESS_OK" in out:
                        print(f"{OK} 명령 실행 (rc={rc})")
                    else:
                        print(f"{WARN} 명령 응답이 예상과 다릅니다: {out[:80]!r}")
                        ok = False
                except Exception as e:                        # noqa: BLE001
                    print(f"{NG} 명령 실행 실패\n     {e}")
                    _raw_dump(src, cfg)
                    ok = False

                # 6. 앱 프로세스
                try:
                    _, out = dut.shell("ps", timeout=15, check=False)
                    if cfg.app_process in out:
                        print(f"{OK} {cfg.app_process} 동작 중")
                    else:
                        print(f"{WARN} {cfg.app_process} 가 안 보입니다")
                        print("       SOFT_CYCLE 자극이 동작하지 않습니다.")
                        print("       duts.yaml 의 app_process 를 확인하세요.")
                except Exception:                             # noqa: BLE001
                    pass
    finally:
        src.stop()

    print(f"\n{'통과' if ok else '확인 필요'}: {name}")
    return ok


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dut", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dut-config", default=str(ROOT / "config" / "duts.yaml"))
    ap.add_argument("--listen", type=float, default=8.0)
    ap.add_argument("--no-login", action="store_true", help="로그 수신까지만 확인")
    a = ap.parse_args()

    entries = load_entries(Path(a.dut_config), None if a.all else a.dut)
    if not entries:
        raise SystemExit(f"--dut {a.dut!r} 를 duts.yaml 에서 못 찾았습니다")
    if not a.all and not a.dut:
        entries = entries[:1]

    # 비밀번호가 없으면 장비를 건드리기 전에 멈춘다.
    # 빈 비밀번호를 보내면 Login incorrect 가 나고 장비가 로그아웃된다.
    if not a.no_login and not any(dut_password(e["name"]) for e in entries):
        print(HOWTO)
        print("\n  로그 수신까지만 확인하려면:  python -m harness.check --all --no-login")
        raise SystemExit(2)

    results = {e["name"]: check_one(e, a.listen, not a.no_login) for e in entries}

    print(f"\n{'=' * 62}")
    for n, r in results.items():
        print(f"  {n:<10} {'통과' if r else '확인 필요'}")
    raise SystemExit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
