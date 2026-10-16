"""
여러 DUT 동시 실행.

    python run_all.py                 duts.yaml 의 전체
    python run_all.py --only crx1,trx1
    python run_all.py -k boot

pytest-xdist 를 쓰지 않는다. DUT 마다 프로세스를 따로 띄운다.
격리가 확실하고, 한 대가 죽어도 나머지가 안 멈춘다.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).parent
ARTIFACTS = ROOT / "artifacts"


def load_duts(path: Path) -> list[dict]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    defaults = raw.get("defaults") or {}
    return [{**defaults, **d} for d in raw.get("duts", [])]


def _find_run_dir(out: Path) -> Path | None:
    """conftest 가 출력한 '산출물 -> <경로>' 줄에서 실행 폴더를 찾는다."""
    try:
        for ln in out.read_text(encoding="utf-8", errors="replace").splitlines():
            if "산출물 ->" in ln:
                p = Path(ln.split("산출물 ->", 1)[1].strip())
                return p if p.is_dir() else None
    except OSError:
        pass
    return None


def _regen_summary(run_dir: Path, dut: dict) -> None:
    """run.out 을 옮긴 뒤 요약을 다시 만든다."""
    try:
        from harness.report import write_summary
        write_summary(run_dir, dut.get("name", "dut"), dut.get("product", "?"))
    except Exception:                                        # noqa: BLE001
        pass


def run_one(dut: dict, args, results: dict) -> None:
    name = dut["name"]
    cmd = [
        sys.executable, "-m", "pytest", "test_spec.py",
        "--mode", args.mode,
        "--source", args.source,
        "--dut", name,
        "--product", dut.get("product", "CRX"),
        "--dut-config", args.dut_config,
        "--spec", args.spec,
        "-q", "--no-header",
    ]
    if args.k:
        cmd += ["-k", args.k]
    if args.no_stimulus:
        cmd += ["--no-stimulus"]
    if args.replay:
        cmd += ["--replay", args.replay]

    # 실행 폴더는 conftest 가 시각으로 만든다. 여기서는 임시 위치에 받아
    # 끝난 뒤 그 폴더로 옮긴다.
    tmp_dir = ARTIFACTS / name
    tmp_dir.mkdir(parents=True, exist_ok=True)
    log_path = tmp_dir / "run.out"
    cmd += ["--junitxml", str(tmp_dir / "result.xml")]

    # Windows 기본 콘솔 인코딩(cp949)으로 나오면 한글 TC 명이 깨진다.
    # 자식 프로세스 출력을 UTF-8 로 고정한다.
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}

    print(f"[{name}] 시작  ({dut.get('product')}, {dut.get('serial_port')})")
    t0 = time.time()
    with log_path.open("w", encoding="utf-8") as f:
        rc = subprocess.run(cmd, cwd=ROOT, env=env,
                            stdout=f, stderr=subprocess.STDOUT).returncode
    # conftest 가 찍은 산출물 경로를 읽어 그쪽으로 옮긴다
    run_dir = _find_run_dir(log_path)
    if run_dir:
        for f in ("run.out", "result.xml"):
            src = tmp_dir / f
            if src.exists():
                try:
                    src.replace(run_dir / f)
                except OSError:
                    pass
        log_path = run_dir / "run.out"
        _regen_summary(run_dir, dut)

    results[name] = (rc, round(time.time() - t0, 1))
    mark = "완료" if rc == 0 else f"실패 (rc={rc})"
    print(f"[{name}] {mark}  {results[name][1]}초  -> {log_path}")
    if rc != 0:
        _explain(name, rc, log_path)


# pytest 종료 코드. 원인이 완전히 다르므로 구분해서 알려준다.
RC_HINT = {
    1: "테스트가 실패했습니다. 아래 내용을 확인하세요.",
    2: "실행이 중단됐습니다 (Ctrl+C 등).",
    3: "내부 오류입니다. 설정 파일 경로나 --dut 이름을 확인하세요.",
    4: "conftest.py 를 로드하지 못했습니다. harness/ 폴더가 제자리에 있는지 확인하세요.",
    5: "수집된 테스트가 0개입니다. --product 값이 시트의 PRODUCT 와 맞는지 확인하세요.",
}


def _explain(name: str, rc: int, log_path: Path) -> None:
    """실패 원인을 화면에 바로 띄운다. 파일만 남기면 4대분을 일일이 열어야 한다."""
    hint = RC_HINT.get(rc, "")
    if hint:
        print(f"         {hint}")
    try:
        lines = [l.rstrip() for l in
                 log_path.read_text(encoding="utf-8", errors="replace").splitlines()]
    except OSError:
        return
    key = [l for l in lines
           if l.startswith(("E   ", "ERROR", "ImportError", "ModuleNotFoundError"))
           or "Error:" in l or "error:" in l]
    for l in (key[:3] or lines[-3:]):
        print(f"         | {l[:150]}")


def _preflight(dut_cfg: Path, spec: Path, mode: str = "live",
               replay: str | None = None) -> None:
    """
    실행 전에 필수 파일을 확인한다. pytest 를 4번 띄워서
    똑같은 오류를 4번 보는 것보다 여기서 한 번 잡는 게 낫다.
    """
    problems = []
    need = ["__init__.py", "spec.py", "dut.py", "verdict.py",
            "logsource.py", "events.py", "store.py", "ports.py"]
    hdir = ROOT / "harness"
    if not hdir.is_dir():
        problems.append(
            "harness/ 폴더가 없습니다.\n"
            "    파일이 루트에 평평하게 풀렸을 수 있습니다.\n"
            f"    {' '.join(need[1:])}\n"
            "    를 harness/ 로 옮기고 빈 harness/__init__.py 를 만드세요.")
    else:
        missing = [f for f in need if not (hdir / f).exists()]
        if missing:
            msg = f"harness/ 에 파일이 없습니다: {', '.join(missing)}"
            # 브라우저가 중복 이름을 'dut (1).py' 로 저장하는 경우가 많다
            dupes = sorted(
                p.name for p in list(hdir.glob("* (*).py")) + list(ROOT.glob("* (*).py")))
            if dupes:
                msg += ("\n    괄호가 붙은 파일이 있습니다: " + ", ".join(dupes)
                        + "\n    같은 파일을 여러 번 받아 브라우저가 이름을 바꾼 것입니다."
                        "\n    괄호 붙은 쪽이 최신 버전이니 원래 이름으로 바꾸세요.")
            problems.append(msg)
    for f in ["conftest.py", "test_spec.py"]:
        if not (ROOT / f).exists():
            problems.append(f"{f} 가 없습니다.")
    if not dut_cfg.exists():
        problems.append(f"DUT 설정이 없습니다: {dut_cfg}")
    if not spec.exists():
        problems.append(f"시트가 없습니다: {spec}")
    if mode == "offline":
        if not replay:
            problems.append("offline 모드에는 --replay 로 로그 파일을 지정해야 합니다.")
        elif not Path(replay).exists():
            found = sorted(p.name for p in ROOT.glob("*.log"))[:5]
            msg = f"로그 파일이 없습니다: {replay}"
            if found:
                msg += "\n    이 폴더에 있는 .log: " + ", ".join(found)
            else:
                msg += "\n    이 폴더에 .log 파일이 없습니다. 전체 경로를 주세요."
            problems.append(msg)
    else:
        try:
            import serial  # noqa: F401
        except ImportError:
            problems.append("pyserial 이 없습니다.  pip install -r requirements.txt")

    if problems:
        print("실행 전 확인이 필요합니다.\n")
        for p in problems:
            print(f"  - {p}")
        raise SystemExit(2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="live", choices=["live", "offline"])
    ap.add_argument("--source", default="direct", choices=["direct", "daemon"],
                    help="live 모드에서 포트를 직접 열지(direct, 기본), "
                         "crx-continuous 상시 프로그램에 접속할지(daemon)")
    ap.add_argument("--dut-config", default=str(ROOT / "config" / "duts.yaml"))
    ap.add_argument("--spec", default=str(ROOT / "spec_v2.xlsx"))
    ap.add_argument("--only", default=None, help="쉼표로 구분한 DUT 이름")
    ap.add_argument("-k", default=None, help="pytest -k 그대로 전달")
    ap.add_argument("--replay", default=None, help="offline 모드용 로그")
    ap.add_argument("--serial", action="store_true", help="한 대씩 순차 실행")
    ap.add_argument("--no-stimulus", action="store_true",
                    help="자극 없이 흐르는 로그만 판정 (에이징 유지)")
    args = ap.parse_args()

    _preflight(Path(args.dut_config), Path(args.spec), args.mode, args.replay)
    duts = load_duts(Path(args.dut_config))
    if args.only:
        want = {x.strip() for x in args.only.split(",")}
        duts = [d for d in duts if d["name"] in want]
    if not duts:
        raise SystemExit("실행할 DUT 가 없습니다")

    results: dict[str, tuple[int, float]] = {}
    print(f"DUT {len(duts)}대 {'순차' if args.serial else '동시'} 실행\n")

    if args.serial:
        for d in duts:
            run_one(d, args, results)
    else:
        threads = [threading.Thread(target=run_one, args=(d, args, results),
                                    name=d["name"]) for d in duts]
        for t in threads:
            t.start()
            time.sleep(2)   # 전원 인가가 겹치지 않게 어긋나게 띄운다
        for t in threads:
            t.join()

    print("\n" + "=" * 52)
    for d in duts:
        n = d["name"]
        rc, sec = results.get(n, (-1, 0))
        print(f"  {n:<8} {d.get('product','?'):<5} "
              f"{'통과' if rc == 0 else '실패':<4} {sec:>7.1f}초")
    print("=" * 52)
    print("결과 화면:  python monitor.py")

    sys.exit(0 if all(rc == 0 for rc, _ in results.values()) else 1)


if __name__ == "__main__":
    main()
