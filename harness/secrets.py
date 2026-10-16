"""
비밀번호 로딩.

우선순위
  1. 환경변수 DUT_PASSWORD
  2. config/secrets.yaml  (git 에 올라가지 않음)

PowerShell 의 $env: 는 그 창에서만 유효해서 창을 새로 열면 날아간다.
매번 다시 치기 번거로우면 secrets.yaml 을 쓴다.
"""

from __future__ import annotations

import os
from pathlib import Path

CONFIG = Path(__file__).parent.parent / "config"
SECRETS = CONFIG / "secrets.yaml"

HOWTO = (
    "비밀번호가 설정되지 않았습니다. 둘 중 하나를 하세요.\n"
    "  1) 영구 환경변수 (권장) — 설정 후 PowerShell 을 새로 여세요\n"
    "     [Environment]::SetEnvironmentVariable('DUT_PASSWORD','비밀번호','User')\n"
    "  2) config/secrets.yaml 파일 생성 (.gitignore 에 포함되어 있음)\n"
    "     dut_password: \"비밀번호\""
)


def dut_password(dut_name: str | None = None) -> str:
    v = os.environ.get("DUT_PASSWORD", "").strip()
    if v:
        return v
    if SECRETS.exists():
        import yaml
        raw = yaml.safe_load(SECRETS.read_text(encoding="utf-8")) or {}
        per = (raw.get("duts") or {})
        if dut_name and per.get(dut_name):
            return str(per[dut_name])
        if raw.get("dut_password"):
            return str(raw["dut_password"])
    return ""
