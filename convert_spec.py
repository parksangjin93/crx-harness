"""
CRX.xlsx -> spec_v2.xlsx 변환.

바꾸는 것
  1. 컬럼 추가: ACTION, ORDERED, SCOPE, BLOCKED_BY
  2. TYPE 에 ACTION 값 허용 (자극을 스텝으로 표현)
  3. 각 TC 맨 앞에 자극 스텝 삽입
  4. PRODUCT 정리 (TC031/032 -> XVM, TC033 -> TRX, 나머지 *)
  5. Jira 167행 BLOCKED_BY 표시
"""

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

SRC = "CRX.xlsx"
DST = "spec_v2.xlsx"

NEW_HEADERS = [
    "출처", "TC_ID", "PRODUCT", "TC_NAME", "TEST_PURPOSE", "STEP",
    "TEST_METHOD", "TYPE", "VALUE", "FAIL", "TIMEOUT", "TIMEOUT_FROM",
    "MATCH", "ORDERED", "SCOPE", "TAG", "CAPTURE", "BLOCKED_BY", "비고",
]

# TC 별 필요한 자극. 로그 분석 결과에 근거해서 배정한다.
#   POWER_CYCLE : 릴레이 하드 전원 사이클 (콜드부트)
#   SOFT_CYCLE  : killall -TERM dvr_app -> SHUTDOWN -> poweroff -> MICOM 재기동
#   HARD_REBOOT : shell reboot (앱 종료가 SIGKILL 로 잘림)
#   NONE        : 부팅 후 정상 동작 중 관측되는 항목
SOFT_CYCLE_TCS = {
    "TC003",   # SHUTDOWN
    "TC011",   # UNMOUNT
    "TC013", "TC014", "TC015",   # RECORD OFF (step2)
    "TC019", "TC020",            # File Close
    "TC004",   # ACC ON 감지 + 녹화 시작
}
ACC_CYCLE = {
    "TC001", "TC002", "TC006", "TC007", "TC008", "TC009", "TC010",
    "TC012", "TC026", "TC030", "TC031", "TC032", "TC033",
    "TC016", "TC017", "TC018", "TC028", "TC034", "TC035",
    "TC036", "TC038", "TC040", "TC041",
    # 2026-09-16: 자극이 없어서 부팅 직후 한 번만 찍히는 로그
    # (BuildUpgradeFile / ExtProbe)를 놓치고 있었다. 4대·매 실행
    # 100% 재현되는 결정론적 실패라 랜덤 캡처 유실이 아니라
    # 매핑 누락이었다. TC008/009/010(부팅 직후 디스크 probe)과
    # 같은 성격이라 ACC_CYCLE 로 묶는다.
    "TC037", "TC039",
    # 2026-09-16: spec_v2.xlsx 전수조사로 찾음. TC037/039 과 똑같이
    # 자극이 없어 'DSM statistics.. total' (주기적 상태 로그)를
    # 임의의 이전 TC 액션 시각 기준으로 기다리다 타이밍이 어긋나면
    # 놓친다. 자극을 줘야 기준 시각이 예측 가능해진다.
    "TC027",
}
# ACC 라인 미분리로 자극 불가
BLOCKED_TCS = {
    "TC005": "ACC 릴레이 미구성",
    # 2026-09-18: 이 랙 DUT 는 WiFi/LAN 으로만 통신한다. 커널 USB 모뎀
    # 드라이버는 매 부팅 로드되지만 qmicli/IsServingCellReady 같은 앱
    # 레벨 셀룰러 통신 로그가 4대 전체 로그에서 0건 - SIM/안테나가 물린
    # 셀룰러 랙이 아니면 이 TC 는 FW 와 무관하게 항상 미검출이다.
    "TC072": "셀룰러 모뎀 미구성 (WiFi/LAN 랙)",
}
# 물리 조작 필요
PHYSICAL_TCS = {
    "TC024": "G센서 충격 자극 필요",
    "TC029": "리모컨/키 입력 필요",
}
# 제품 한정
PRODUCT_MAP = {"TC031": "XVM", "TC032": "XVM", "TC033": "TRX"}

# 순서 무관 TC (스텝이 동시에/임의 순서로 나옴)
UNORDERED_TCS = {"TC007", "TC013", "TC014", "TC015", "TC039"}

# 실측 기반 TIMEOUT 보정. (TC_ID, STEP) -> 초
# 근거: 2026-09-01 에이징 로그 4회 부팅 측정
#   Loader Start -> (LogWriter) BOOT      44.2 ~ 45.0s
#   Loader Start -> DSM Initialize        41.0 ~ 41.8s
#   Record[INT] Off -> ACC ON 재기동      101.4 ~ 106.8s
TIMEOUT_FIX = {
    ("TC004", 1): 240,   # SOFT_CYCLE 후 재기동까지 실측 max 106.8s
    ("TC026", 2): 90,    # 실측 max 41.8s
}

# 시트 VALUE 교정이 필요할 때 쓴다. (TC_ID, STEP) -> 올바른 VALUE
# 반드시 실제 로그로 확인한 뒤에만 넣는다.
VALUE_FIX: dict[tuple[str, int], str] = {}

# 자극 없이 서버/외부가 일으키는 이벤트. 로그 전체를 스캔한다.
SCAN_ANY_TCS = {"FOTA001", "FOTA002", "FOTA003", "FOTA004", "FOTA005"}

# CAPTURE: 이 스텝이 통과할 때 값을 뽑아 실행 레코드에 붙인다.
# 추이 대시보드의 축이 된다. (TC_ID, STEP) -> "이름" 또는 "이름:정규식"
# 근거: 실제 로그 문구 "(LogWriter) FW_VERSION 1.3.0.RC1"
CAPTURE_MAP = {
    ("TC002", 1): r"fw_version:FW_VERSION\s+(\S+)",
    ("TC001", 4): "boot_ok",
}


def stimulus_for(tc_id: str) -> str | None:
    if tc_id in SOFT_CYCLE_TCS:
        return "SOFT_CYCLE"
    if tc_id in ACC_CYCLE:
        return "ACC_CYCLE"
    return None


def main() -> None:
    src = openpyxl.load_workbook(SRC, data_only=True)
    sws = src.active
    rows = [[sws.cell(r, c).value for c in range(1, 15)]
            for r in range(2, sws.max_row + 1)]
    rows = [r for r in rows if any(v is not None for v in r)]

    dst = openpyxl.Workbook()
    ws = dst.active
    ws.title = "SPEC"

    hdr_font = Font(name="맑은 고딕", size=11, bold=True)
    hdr_fill = PatternFill("solid", fgColor="D9E1F2")
    body_font = Font(name="맑은 고딕", size=11)
    new_fill = PatternFill("solid", fgColor="FFF2CC")   # 새로 추가한 셀 표시

    for i, h in enumerate(NEW_HEADERS, start=1):
        c = ws.cell(1, i, h)
        c.font = hdr_font
        c.fill = hdr_fill
        c.alignment = Alignment(horizontal="center", vertical="center")

    out_r = 2
    seen_tc: set[str] = set()

    for r in rows:
        src_, tc, _prod, name, purpose, step, method, typ, val, fail, to, match, tag, note = r
        tc = str(tc)

        automatable = val is not None
        blocked = None
        if not automatable:
            blocked = "검증 조건 미정의(Jira 이슈 원문)"
        elif tc in BLOCKED_TCS:
            blocked = BLOCKED_TCS[tc]
        elif tc in PHYSICAL_TCS:
            blocked = PHYSICAL_TCS[tc]

        product = PRODUCT_MAP.get(tc, "*")
        ordered = "N" if tc in UNORDERED_TCS else "Y"
        scope = "TC" if typ == "ABSENT" else ""

        # TC 의 첫 등장이고 자극이 필요하면 ACTION 행을 먼저 넣는다
        if tc not in seen_tc:
            seen_tc.add(tc)
            stim = stimulus_for(tc) if automatable and not blocked else None
            if stim:
                act = [src_, tc, product, name, purpose, 0, f"자극: {stim}",
                       "ACTION", stim, None, None, None, None,
                       ordered, "", tag, None, None, "자동 생성된 자극 스텝"]
                for i, v in enumerate(act, start=1):
                    c = ws.cell(out_r, i, v)
                    c.font = body_font
                    if i in (8, 9, 12, 14, 15, 17, 18):
                        c.fill = new_fill
                out_r += 1

        # TIMEOUT_FROM: 첫 CHECK 는 자극 완료 시각, 이후는 직전 스텝 매칭 시각
        if typ == "ACTION":
            tf = None
        elif tc in SCAN_ANY_TCS:
            tf = "ANY"
        elif int(step or 1) == 1:
            tf = "ACTION"
        else:
            tf = "PREV_STEP" if ordered == "Y" else "ACTION"

        key = (tc, int(step or 0))
        if key in TIMEOUT_FIX:
            to = TIMEOUT_FIX[key]
            note = f"{note or ''} / TIMEOUT 실측 보정".strip(" /")
        if key in VALUE_FIX:
            note = f"{note or ''} / VALUE 오탈자 수정(원본: {val})".strip(" /")
            val = VALUE_FIX[key]

        vals = [src_, tc, product, name, purpose, step, method, typ, val,
                fail, to, tf, match, ordered, scope, tag,
                CAPTURE_MAP.get(key), blocked, note]
        for i, v in enumerate(vals, start=1):
            c = ws.cell(out_r, i, v)
            c.font = body_font
            if i in (12, 14, 15, 17, 18):
                c.fill = new_fill
        out_r += 1

    widths = {1: 11, 2: 10, 3: 9, 4: 40, 5: 30, 6: 6, 7: 28, 8: 9, 9: 42,
              10: 26, 11: 9, 12: 13, 13: 11, 14: 9, 15: 7, 16: 20, 17: 28,
              18: 26, 19: 46}
    for col, w in widths.items():
        ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = w
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:S{out_r - 1}"

    # 범례 시트
    lg = dst.create_sheet("LEGEND")
    lg.column_dimensions["A"].width = 16
    lg.column_dimensions["B"].width = 78
    legend = [
        ("컬럼", "설명"),
        ("TYPE", "CHECK=로그가 나와야 함 / ABSENT=나오면 안 됨 / ACTION=제품에 가하는 자극"),
        ("VALUE", "CHECK·ABSENT: 찾을 문자열 / ACTION: 자극 이름"),
        ("ACTION 값", "ACC_CYCLE=릴레이 전원 사이클 / SOFT_CYCLE=killall -TERM dvr_app 후 자동 poweroff·재기동 / HARD_REBOOT=shell reboot / SHELL:<명령> / WAIT:<초>"),
        ("TIMEOUT", "초 단위. 비면 config 의 default_timeout_s 사용"),
        ("TIMEOUT_FROM", "ACTION=마지막 자극 완료 시각 기준 / PREV_STEP=직전 스텝 매칭 시각 기준"),
        ("ORDERED", "Y=스텝이 시트 순서대로 나와야 함 / N=순서 무관"),
        ("SCOPE", "ABSENT 전용. TC=테스트 실행 구간 전체에서 검사"),
        ("CAPTURE", "이 스텝 통과 시 값을 뽑아 실행 레코드에 저장한다. '이름' 또는 '이름:정규식'. 추이 대시보드의 축이 된다"),
        ("BLOCKED_BY", "값이 있으면 자동 실행하지 않고 blocked 로 집계한다. 비워두면 실행 대상"),
        ("PRODUCT", "* = 전 제품 공통 / CRX·TRX·XVM = 해당 제품만"),
        ("", ""),
        ("주황색 셀", "v2 에서 새로 추가된 항목"),
        ("", ""),
        ("근거", "SOFT_CYCLE 배정은 2026-09-01 에이징 로그 실측에 근거함"),
        ("", "  RECORD OFF -> SHUTDOWN 소요 45.6초, 이후 poweroff -> MICOM 이 ACC ON 으로 재기동"),
        ("", "HARD_REBOOT(shell reboot)는 0.35초 만에 SIGKILL 되어 SHUTDOWN·UNMOUNT 미도달"),
    ]
    for i, (a, b) in enumerate(legend, start=1):
        lg.cell(i, 1, a).font = Font(name="맑은 고딕", size=11, bold=(i == 1))
        lg.cell(i, 2, b).font = Font(name="맑은 고딕", size=11, bold=(i == 1))
        lg.cell(i, 2).alignment = Alignment(wrap_text=True, vertical="top")

    dst.save(DST)
    print(f"saved {DST}: {out_r-1} rows")


if __name__ == "__main__":
    main()
