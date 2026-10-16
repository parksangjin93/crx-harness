"""실제 장비·회사 로그 없이 실행할 가상 Excel 명세를 생성한다."""

from pathlib import Path
import sys

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.spec import HEADERS


def main():
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "SPEC"
    sheet.append(HEADERS)

    def row(tc_id, name, step, kind, value, **options):
        fields = {
            "출처": "synthetic demo",
            "TC_ID": tc_id,
            "PRODUCT": "DEMO",
            "TC_NAME": name,
            "TEST_PURPOSE": "가상 로그로 판정 경로 확인",
            "STEP": step,
            "TYPE": kind,
            "VALUE": value,
            "TIMEOUT": 3,
            "TIMEOUT_FROM": "ACTION",
            "MATCH": "CONTAINS",
            "ORDERED": "Y",
            **options,
        }
        sheet.append([fields.get(header) for header in HEADERS])

    row("DEMO001", "boot and ready", 1, "CHECK", "DEMO_BOOT")
    row("DEMO001", "boot and ready", 2, "CHECK", "DEMO_READY")
    row("DEMO002", "no error until ready", 1, "CHECK", "DEMO_READY")
    row("DEMO002", "no error until ready", 2, "ABSENT", "DEMO_ERROR", SCOPE="TC")
    row("DEMO003", "physical input unavailable", 1, "CHECK", "DEMO_BUTTON",
        BLOCKED_BY="가상 예제에는 물리 입력 장비 없음")
    row("DEMO004", "expected missing event", 1, "CHECK", "DEMO_MISSING",
        TAG="regression_watch")

    path = Path(__file__).with_name("spec.demo.xlsx")
    workbook.save(path)
    workbook.close()
    print(f"가상 명세 생성: {path}")


if __name__ == "__main__":
    main()
