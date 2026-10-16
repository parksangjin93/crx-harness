"""
금지 패턴 튜닝 도구.

    python -m harness.scan <로그파일>
    python -m harness.scan <로그파일> --context 12

캡처된 로그에 forbidden.yaml 패턴이 몇 번, 어디서 걸리는지 보여준다.
known_issues.yaml 로 이미 제외되는 건 따로 표시한다.

제품마다 정상 상태에서 나오는 커널 메시지가 다르다.
튜닝 없이 금지 패턴을 켜면 전 테스트가 100% 실패한다.
새 제품을 붙일 때마다 이걸 먼저 돌린다.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
CONFIG = ROOT / "config"
CONTEXT_LINES = 40      # test_spec.py 와 같은 값


def load_rules() -> tuple[list[tuple[re.Pattern, str]], list[dict]]:
    fb = yaml.safe_load((CONFIG / "forbidden.yaml").read_text(encoding="utf-8"))
    ki = yaml.safe_load((CONFIG / "known_issues.yaml").read_text(encoding="utf-8"))
    rules = [(re.compile(f["pattern"]), f["desc"]) for f in fb["forbidden"]]
    known = ki.get("known_issues") or []
    for k in known:
        k["_rx"] = re.compile(k["pattern"])
    return rules, known


def scan(path: Path, context: int = 6) -> int:
    rules, known = load_rules()
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    print(f"{path.name}  ({len(lines):,}줄)\n")

    hits: dict[str, list[int]] = {}
    excused: dict[str, list[int]] = {}

    for i, text in enumerate(lines):
        for rx, desc in rules:
            if not rx.search(text):
                continue
            ctx = "\n".join(lines[max(0, i - 5): i + CONTEXT_LINES])
            by = next((k for k in known if k["_rx"].search(ctx)), None)
            key = f"{desc} :: {rx.pattern}"
            if by:
                excused.setdefault(f"{key} -> {by['id']}", []).append(i)
            else:
                hits.setdefault(key, []).append(i)

    if excused:
        print("[제외됨] known_issues.yaml 에 등록된 항목")
        for key, idxs in excused.items():
            print(f"  {len(idxs):>5}건  {key}")
        print()

    if not hits:
        print("[통과] 걸리는 금지 패턴이 없습니다.")
        return 0

    print(f"[검출] {len(hits)}종 — 아래를 보고 결정하세요\n")
    for key, idxs in hits.items():
        print(f"  {len(idxs):>5}건  {key}")
    print()

    for key, idxs in hits.items():
        print("=" * 74)
        print(f"{key}   ({len(idxs)}건, 첫 발생 {idxs[0] + 1}행)")
        print("=" * 74)
        i = idxs[0]
        for n in range(max(0, i - 2), min(len(lines), i + context)):
            mark = ">>" if n == i else "  "
            print(f"{mark} {n + 1:>7} {lines[n][:150]}")
        print()

    print("-" * 74)
    print("판단 기준")
    print("  매 부팅마다 나오고 동작에 문제가 없다  -> known_issues.yaml 에 등록")
    print("  이번 로그에서만 나온다                 -> 실제 결함. 티켓")
    print()
    print("등록할 때는 pattern 에 그 스택/문맥에서만 나오는 고유 문자열을 쓰세요.")
    print("(드라이버 이름, 함수명 등. 'Call trace:' 같은 공통 문구는 안 됩니다)")
    print("ticket 과 evidence 를 반드시 채우세요. 근거 없는 예외가 쌓이면")
    print("금지 패턴 전체가 무력화됩니다.")
    return 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("logfile")
    ap.add_argument("--context", type=int, default=6, help="앞뒤로 보여줄 줄 수")
    a = ap.parse_args()
    p = Path(a.logfile)
    if p.is_dir():
        marker = p / "latest.txt"
        if marker.exists() and (p / marker.read_text(encoding="utf-8").strip()).exists():
            p = p / marker.read_text(encoding="utf-8").strip()
        else:
            logs = sorted(p.glob("*.log"), key=lambda x: x.stat().st_mtime)
            if not logs:
                raise SystemExit(f"폴더에 .log 가 없습니다: {a.logfile}")
            p = logs[-1]
        print(f"최신 로그 선택: {p}")
    if not p.exists():
        found = sorted(x.name for x in ROOT.glob("*.log"))[:8]
        print(f"파일이 없습니다: {a.logfile}")
        if found:
            print("이 폴더의 .log: " + ", ".join(found))
        raise SystemExit(2)
    raise SystemExit(scan(p, a.context))


if __name__ == "__main__":
    main()
