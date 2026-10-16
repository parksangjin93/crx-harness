"""
실행 산출물 정리.

실행 하나 = 폴더 하나. UART 로그와 판정 결과를 같은 곳에 둔다.
따로 두면 "9월 8일 14시 실행에서 TC008 이 왜 실패했나"를 볼 때
두 폴더를 오가며 시각을 맞춰야 한다.

    C:/qa_logs/CRX3212/2026-09-08-14시23분05초/
        uart.log            원문 (회전 시 .002.log ...)
        events.jsonl
        result.xml
        run.out
        summary.txt         사람이 먼저 보는 요약
        failures/TC008.log  실패 구간만 잘라낸 로그
"""

from __future__ import annotations

import json
import re
from pathlib import Path

TS = re.compile(r"^(\d{2}):(\d{2}):(\d{2})\.(\d{3})\s*\|\s*([\d.-]+)\s*\|")
# 기존 캡처 툴 형식: [2026-09-01-13시17분49.871초]
TS_OLD = re.compile(r"^\[\d{4}-\d{2}-\d{2}-(\d{2})시(\d{2})분([\d.]+)초\]")


def _elapsed(line: str) -> float | None:
    m = TS.match(line)
    return float(m.group(5)) if m else None


def _wall(line: str) -> float | None:
    m = TS.match(line)
    if m:
        h, mi, s, ms = (int(m.group(i)) for i in range(1, 5))
        return h * 3600 + mi * 60 + s + ms / 1000.0
    o = TS_OLD.match(line)
    if o:
        return int(o.group(1)) * 3600 + int(o.group(2)) * 60 + float(o.group(3))
    return None


def write_summary(run_dir: Path, dut: str, product: str) -> Path:
    """events.jsonl 을 읽어 summary.txt 와 failures/ 를 만든다."""
    run_dir = Path(run_dir)
    ev_path = run_dir / "events.jsonl"
    if not ev_path.exists():
        return run_dir / "summary.txt"

    events = []
    for raw in ev_path.read_text(encoding="utf-8", errors="replace").splitlines():
        raw = raw.strip()
        if raw:
            try:
                events.append(json.loads(raw))
            except json.JSONDecodeError:
                pass

    fw = next((e.get("value") for e in events
               if e.get("kind") == "capture" and e.get("key") == "fw_version"), None)
    started = next((e["ts"] for e in events if e.get("kind") == "run_start"), None)
    ended = next((e["ts"] for e in events if e.get("kind") == "run_end"), None)

    tally = {"passed": 0, "failed": 0, "skipped": 0, "infra": 0}
    fails = []
    for e in events:
        if e.get("kind") != "tc_done":
            continue
        oc = e.get("outcome", "failed")
        tally[oc] = tally.get(oc, 0) + 1
        if oc in ("failed", "infra"):
            fails.append(e)

    # regression_watch 태그가 붙은 TC 는 원래 계속 실패하는 게 정상인
    # 회귀 감시용이라, 진짜 새 실패와 섞이지 않게 따로 묶는다.
    watch = [f for f in fails if "regression_watch" in (f.get("tags") or [])]
    watch_ids = {f.get("tc_id") for f in watch}
    real_fails = [f for f in fails if f.get("tc_id") not in watch_ids]
    watch_failed = sum(1 for f in watch if f.get("outcome") == "failed")
    watch_infra = sum(1 for f in watch if f.get("outcome") == "infra")

    # 실패 TC 별 로그 구간 추출
    cut = _slice_failures(run_dir, events, [f.get("tc_id") for f in fails])

    import datetime as dt
    def fmt(x):
        return dt.datetime.fromtimestamp(x).strftime("%Y-%m-%d %H:%M:%S") if x else "?"

    lines = [
        f"{dut} ({product})  {fmt(started)} ~ {fmt(ended)}",
        f"FW {fw or '?'}   "
        f"{tally['passed']} passed / {tally['failed'] - watch_failed} failed / "
        f"{tally['skipped']} skipped"
        + (f" / {tally['infra'] - watch_infra} 장비오류" if tally.get("infra", 0) - watch_infra > 0 else "")
        + (f" / {len(watch)} 회귀감시" if watch else ""),
        "",
    ]
    if real_fails:
        lines.append("실패")
        for f in real_fails:
            tc = f.get("tc_id", "?")
            reason = _short_reason(f.get("reason", ""))
            tag = "  -> failures/%s.log" % tc if tc in cut else ""
            lines.append(f"  {tc:<9} {reason}{tag}")
    else:
        lines.append("실패 없음")
    if watch:
        lines.append("")
        lines.append("회귀 감시 중 (예상된 실패 - 원인 해결 전까지 정상 상태)")
        for f in watch:
            tc = f.get("tc_id", "?")
            reason = _short_reason(f.get("reason", ""))
            tag = "  -> failures/%s.log" % tc if tc in cut else ""
            lines.append(f"  {tc:<9} {reason}{tag}")

    out = run_dir / "summary.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_detail_report(run_dir, dut, product, events=events, fw=fw,
                        started=started, ended=ended)
    return out


def write_detail_report(run_dir: Path, dut: str, product: str,
                        events: list[dict] | None = None,
                        fw: str | None = None,
                        started: float | None = None,
                        ended: float | None = None) -> Path:
    """
    TC 별 스텝 단위 상세 - 무엇을 기대했고, 실제로 어떤 로그 줄이 언제
    그걸 충족시켰는지 - 를 사람이 읽을 Markdown 문서로 남긴다.
    통과한 TC 도 근거를 남겨야 "어떻게 확인됐는지" 팀/매니저에게 보여줄 수
    있다 - summary.txt 는 실패만 보여주고 통과분 근거는 안 남기기 때문에
    따로 둔다. write_summary() 가 매 실행마다 자동으로 이것도 만든다.
    """
    run_dir = Path(run_dir)
    out = run_dir / "detail_report.md"
    if events is None:
        ev_path = run_dir / "events.jsonl"
        if not ev_path.exists():
            return out
        events = []
        for raw in ev_path.read_text(encoding="utf-8", errors="replace").splitlines():
            raw = raw.strip()
            if raw:
                try:
                    events.append(json.loads(raw))
                except json.JSONDecodeError:
                    pass
        fw = next((e.get("value") for e in events
                  if e.get("kind") == "capture" and e.get("key") == "fw_version"), None)
        started = next((e["ts"] for e in events if e.get("kind") == "run_start"), None)
        ended = next((e["ts"] for e in events if e.get("kind") == "run_end"), None)

    tc_info: dict[str, dict] = {}
    order: list[str] = []
    for e in events:
        k = e.get("kind")
        tid = e.get("tc_id")
        if not tid:
            continue
        if tid not in tc_info:
            tc_info[tid] = {"name": "", "tags": [], "outcome": None,
                            "duration_s": None, "steps": [], "absent_hits": []}
            order.append(tid)
        info = tc_info[tid]
        if k == "tc_start":
            info["name"] = e.get("name", "") or info["name"]
            info["tags"] = e.get("tags") or info["tags"]
        elif k == "tc_steps":
            info["steps"] = e.get("steps", [])
            info["absent_hits"] = e.get("absent_hits", [])
        elif k == "tc_done":
            info["name"] = e.get("name", "") or info["name"]
            info["tags"] = e.get("tags") or info["tags"]
            info["outcome"] = e.get("outcome")
            info["duration_s"] = e.get("duration_s")

    import datetime as dt
    def fmt(x):
        return dt.datetime.fromtimestamp(x).strftime("%Y-%m-%d %H:%M:%S") if x else "?"

    OUTCOME_LABEL = {"passed": "PASS", "failed": "FAIL",
                    "infra": "장비오류", "skipped": "SKIP"}

    lines = [
        f"# {dut} ({product}) 상세 검증 리포트",
        "",
        f"- 실행: {fmt(started)} ~ {fmt(ended)}",
        f"- FW: {fw or '?'}",
        "",
    ]

    for tid in order:
        info = tc_info[tid]
        outcome = info.get("outcome")
        watch = "regression_watch" in (info.get("tags") or [])
        label = OUTCOME_LABEL.get(outcome, outcome or "?")
        if watch and outcome == "failed":
            label = "XFAIL (회귀 감시 중 - 정상 상태)"
        lines.append(f"## {tid} — {label}")
        if info.get("name"):
            lines.append(f"- 이름: {info['name']}")
        if info.get("duration_s") is not None:
            lines.append(f"- 소요: {info['duration_s']}s")
        steps = info.get("steps") or []
        if steps:
            lines.append("")
            lines.append("| step | type | 기대(match/value) | 결과 | 근거(시각/로그) |")
            lines.append("|---|---|---|---|---|")
            for s in steps:
                mark = "PASS" if s.get("passed") else "FAIL"
                expect = f"{s.get('match', '')} `{str(s.get('value', ''))[:60]}`"
                if s.get("passed"):
                    at = s.get("at")
                    at_s = f"{at:.1f}s" if at is not None else "-"
                    line_txt = (s.get("line") or "")[:90].replace("|", "\\|")
                    evidence = f"{at_s} — `{line_txt}`"
                else:
                    evidence = (s.get("reason") or "")[:120].replace("|", "\\|")
                lines.append(f"| {s.get('no', '')} | {s.get('type', '')} | {expect} "
                            f"| {mark} | {evidence} |")
        if info.get("absent_hits"):
            lines.append("")
            lines.append("**ABSENT 위반:**")
            for h in info["absent_hits"]:
                lines.append(f"- {h}")
        lines.append("")

    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def _short_reason(reason: str) -> str:
    """pytest 출력에서 사람이 볼 한 줄만 뽑는다."""
    lines = reason.splitlines()
    for ln in lines:
        ln = ln.strip()
        if ln.startswith("사유:"):
            return ln
        if "FAIL 문구" in ln or "금지 패턴" in ln:
            return ln
    # 예외로 실패한 경우(pytest.fail 의 사유 문구가 아니라 파이썬 예외)
    # pytest 는 실제 예외를 "E " 로 표시한다. 이걸 안 보고 아래 폴백으로
    # 넘어가면 트레이스백 맨 앞의 지역변수 덤프("self = <object at 0x..>")
    # 가 뽑혀서 전혀 쓸모없는 사유가 나온다 (2026-10 daemon 모드에서
    # ConnectionResetError 실측으로 발견).
    # 메시지가 여러 줄이면(예: InfraError 안에 \n 이 있는 경우) pytest 는
    # 줄마다 "E " 를 붙인다 - 그래서 마지막 한 줄만 보면 안 되고, 마지막
    # "E " 연속 구간(=체인된 예외 중 실제로 전파된 마지막 것) 전체를
    # 이어붙여야 온전한 메시지가 된다.
    e_blocks: list[list[str]] = []
    cur: list[str] = []
    for ln in lines:
        s = ln.strip()
        if s == "E" or s.startswith("E "):
            cur.append(s[1:].strip())
        elif cur:
            e_blocks.append(cur)
            cur = []
    if cur:
        e_blocks.append(cur)
    if e_blocks:
        return " ".join(e_blocks[-1])[:200]
    for ln in lines:
        ln = ln.strip()
        if ln and not ln.startswith(("[", "=")):
            return ln[:110]
    return "(사유 없음)"


def _slice_failures(run_dir: Path, events: list[dict],
                    tc_ids: list[str]) -> set[str]:
    """
    실패한 TC 의 로그 구간만 잘라 failures/<TC>.log 로 남긴다.
    200MB 파일에서 해당 시각을 손으로 찾는 일이 없어진다.
    """
    logs = sorted(run_dir.glob("uart*.log"))
    if not logs:
        # 오프라인 재생은 실행 폴더에 UART 로그가 없다. 재생 원본을 쓴다.
        rep = next((e.get("replay") for e in events
                    if e.get("kind") == "run_start" and e.get("replay")), None)
        if rep:
            rp = Path(rep)
            if rp.is_dir():
                mk = rp / "latest.txt"
                if mk.exists():
                    rp = rp / mk.read_text(encoding="utf-8").strip()
                else:
                    cand = sorted(rp.glob("*.log"), key=lambda x: x.stat().st_mtime)
                    rp = cand[-1] if cand else rp
            if rp.is_file():
                logs = [rp]
    if not logs or not tc_ids:
        return set()

    lines: list[str] = []
    for p in logs:
        lines += p.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        return set()

    # tc_id -> (시작 인덱스, 끝 인덱스). 이벤트의 상대초로 로그 위치를 찾는다.
    marks: dict[str, list[float]] = {}
    cur = None
    for e in events:
        k = e.get("kind")
        if k == "tc_start":
            cur = e.get("tc_id")
            marks.setdefault(cur, [None, None])
        elif k == "step_done" and cur and e.get("at") is not None:
            marks[cur][1] = e["at"]
            if marks[cur][0] is None:
                marks[cur][0] = e["at"]
        elif k == "waiting" and cur and e.get("elapsed") is not None:
            # 실패한 스텝은 at 이 없다. 대기 중 찍힌 진행 로그로 구간을 잡는다.
            if marks[cur][0] is None:
                marks[cur][0] = e["elapsed"]
            marks[cur][1] = e["elapsed"]

    walls = [_wall(l) for l in lines]
    done = set()
    fdir = run_dir / "failures"
    for tc in tc_ids:
        if not tc:
            continue
        span = marks.get(tc)
        # 판정 시각을 못 잡으면 마지막 400줄을 남긴다. 없는 것보단 낫다.
        if not span or span[0] is None:
            chunk = lines[-400:]
        else:
            lo, hi = span[0], (span[1] or span[0]) + 5
            idx = [i for i, w in enumerate(walls) if w is not None]
            sel = [i for i in idx if lo - 5 <= (walls[i] or 0) <= hi]
            chunk = [lines[i] for i in sel][-2000:] or lines[-400:]
        fdir.mkdir(parents=True, exist_ok=True)
        (fdir / f"{tc}.log").write_text("\n".join(chunk) + "\n", encoding="utf-8")
        done.add(tc)
    return done
