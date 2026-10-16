"""
실행 결과 누적 저장소.

단발 리포트로는 "이번 빌드에서 부팅이 3초 느려졌다"를 볼 수 없다.
추이를 보려면 결과가 쌓여야 한다.

축은 실행 시각이 아니라 FW 버전이다. 시각으로 잡으면
"언제 나빠졌는지"는 알아도 "어느 빌드에서"는 모른다.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  REAL NOT NULL,
    ended_at    REAL,
    dut         TEXT,
    product     TEXT,
    mode        TEXT,
    fw_version  TEXT,
    passed      INTEGER DEFAULT 0,
    failed      INTEGER DEFAULT 0,
    skipped     INTEGER DEFAULT 0,
    infra       INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tc_results (
    run_id      INTEGER NOT NULL,
    tc_id       TEXT NOT NULL,
    name        TEXT,
    outcome     TEXT,
    duration_s  REAL,
    reason      TEXT,
    PRIMARY KEY (run_id, tc_id)
);
CREATE TABLE IF NOT EXISTS captures (
    run_id      INTEGER NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT,
    PRIMARY KEY (run_id, key)
);
CREATE INDEX IF NOT EXISTS ix_tc ON tc_results(tc_id);
"""


class Store:
    def __init__(self, path: str | Path = "artifacts/runs.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(self.path), check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)
        self.con.commit()

    # ---------- 적재 ----------

    def ingest(self, events: list[dict]) -> int | None:
        """events.jsonl 한 실행분을 통째로 넣는다. run_id 를 돌려준다."""
        start = next((e for e in events if e["kind"] == "run_start"), None)
        if start is None:
            return None

        cur = self.con.execute(
            "INSERT INTO runs (started_at, dut, product, mode) VALUES (?,?,?,?)",
            (start["ts"], start.get("dut"), start.get("product"), start.get("mode")),
        )
        run_id = cur.lastrowid
        counts = {"passed": 0, "failed": 0, "skipped": 0, "infra": 0}
        ended = None

        for e in events:
            k = e["kind"]
            if k == "tc_done":
                outcome = e.get("outcome", "failed")
                counts[outcome] = counts.get(outcome, 0) + 1
                self.con.execute(
                    "INSERT OR REPLACE INTO tc_results VALUES (?,?,?,?,?,?)",
                    (run_id, e.get("tc_id"), e.get("name"), outcome,
                     e.get("duration_s"), (e.get("reason") or "")[:800]),
                )
            elif k == "capture":
                self.con.execute(
                    "INSERT OR REPLACE INTO captures VALUES (?,?,?)",
                    (run_id, e.get("key"), str(e.get("value"))),
                )
            elif k == "run_end":
                ended = e["ts"]

        fw = self.con.execute(
            "SELECT value FROM captures WHERE run_id=? AND key='fw_version'", (run_id,)
        ).fetchone()
        self.con.execute(
            "UPDATE runs SET ended_at=?, fw_version=?, passed=?, failed=?, "
            "skipped=?, infra=? WHERE id=?",
            (ended, fw["value"] if fw else None, counts["passed"], counts["failed"],
             counts["skipped"], counts["infra"], run_id),
        )
        self.con.commit()
        return run_id

    # ---------- 조회 ----------

    def runs(self, limit: int = 50) -> list[dict]:
        rows = self.con.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def capture_series(self, key: str, limit: int = 50) -> list[dict]:
        rows = self.con.execute(
            "SELECT r.id, r.started_at, r.fw_version, c.value "
            "FROM captures c JOIN runs r ON r.id=c.run_id "
            "WHERE c.key=? ORDER BY r.id DESC LIMIT ?", (key, limit),
        ).fetchall()
        out = []
        for r in reversed(rows):
            try:
                v = float(r["value"])
            except (TypeError, ValueError):
                continue
            out.append({"run_id": r["id"], "fw": r["fw_version"] or "?", "value": v})
        return out

    def flaky(self, limit: int = 15) -> list[dict]:
        """
        자꾸 깨지는 TC. 가끔 실패하는 테스트를 방치하면
        팀이 리포트 전체를 불신하게 된다.
        """
        rows = self.con.execute(
            "SELECT tc_id, name, COUNT(*) n, "
            "SUM(CASE WHEN outcome='failed' THEN 1 ELSE 0 END) f "
            "FROM tc_results GROUP BY tc_id HAVING n >= 2 AND f > 0 "
            "ORDER BY (f*1.0/n) DESC, f DESC LIMIT ?", (limit,),
        ).fetchall()
        return [
            {"tc_id": r["tc_id"], "name": r["name"], "runs": r["n"],
             "fails": r["f"], "rate": round(r["f"] / r["n"] * 100, 1)}
            for r in rows
        ]

    def last_failures(self, run_id: int) -> list[dict]:
        rows = self.con.execute(
            "SELECT tc_id, name, reason FROM tc_results "
            "WHERE run_id=? AND outcome='failed' ORDER BY tc_id", (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]
