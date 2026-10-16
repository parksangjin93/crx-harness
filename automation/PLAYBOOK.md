# 자동 회귀 루프 — 실행 지침 (2026-10-06 갱신판, 2시간 주기)

이 파일은 ScheduleWakeup으로 깨어날 때마다(최대 1시간 간격) 참고할 절차다.
대화가 압축(compact)되어도 이 파일과 `state.json`, `CHANGELOG.md`만 읽으면 이어서 진행할 수 있어야 한다.

이전(9/23~9/29 추석 연휴) 루프와 거의 같은 구조이지만 주기와 실행 커맨드가 다르다. 아래만 다름:
- 목표 주기 6시간 → **2시간**
- 실행 커맨드에 **`--source daemon` 필수** (아래 "왜 daemon 모드여야 하는가" 참고)

## 기본 파라미터
- 시작일: 2026-10-06
- 종료 조건: **2026-10-07 09:00 (KST)** 이후에는 새 사이클을 시작하지 않음. 진행 중이던 사이클만 마무리.
- 목표 주기: **2시간마다** `python run_all.py --source daemon` 1회 실행 (4대 DUT 동시)
- ScheduleWakeup은 1회 최대 3600초(1시간)만 예약 가능 → 매시간 깨어나서 `automation/state.json`을 보고 "지금 뭘 할 차례인지" 판단한다.

## 왜 `--source daemon` 이어야 하는가 (중요, 절대 생략하지 말 것)
`continuous_log.py`(crx-continuous 상시 모니터링 프로그램, 4개 DUT 전부에서 상시 가동 중)가 COM 포트를 이미
점유하고 있다. `--mode live`의 기본값인 `--source direct`로 돌리면 `SerialLogSource`가 같은 포트를 또
열려고 해서 포트 공유 경합이 생기고, 그 결과 (2026-10-06 11:59 실행에서 실제 확인된 증상):
- ACC Remove/SHUTDOWN 같은 신호가 타임아웃을 한참(수백 초) 넘겨서야 캡처됨
- `(LogWriter) BOOT` 같은 로그가 통째로 누락
- 로그 인터리빙이 악화되어 `ensure_login()` 류 상태기계 버그가 평소보다 훨씬 자주 터짐

`--source daemon`을 쓰면 포트를 직접 안 열고 `continuous_log.py`가 쓰는 로그 파일을 경유해서 읽으므로
이 경합이 없다. **이번 루프의 각 사이클은 반드시 `--source daemon`으로 실행할 것.**

## 상태 파일: automation/state.json
```
{
  "phase": "idle" | "running" | "analyzing",
  "cycle": <int>,
  "last_run_start": "<ISO8601>" | null,
  "last_run_complete": "<ISO8601>" | null,
  "run_dirs": {"CRX3212": "...", "TRX3208": "...", "VMC8PRO": "...", "XVM508": "..."} | null,
  "end_date": "2026-10-07T09:00:00",
  "interval_hours": 2,
  "source_mode": "daemon"
}
```

## 매 깨어남(wakeup)마다 할 일

1. `Get-Date`로 현재 시각 확인, `automation/state.json` 읽기.
2. `now >= end_date` 이고 phase == "idle" 이면: CHANGELOG.md에 종료 기록 남기고 `ScheduleWakeup(stop: true)`. 끝.
3. **phase == "running"**: 4개 DUT 로그 폴더(`C:\Users\HW-RACK\Desktop\log\<DUT>\`)에서 `last_run_start` 이후 새로 생긴 폴더에 `summary.txt`가 다 생겼는지 확인.
   - 아직이면: 그냥 재대기 (`ScheduleWakeup(delaySeconds: 3600, noop: true)`), phase 그대로.
   - 다 끝났으면: **phase="analyzing"으로 전환하고 바로 이어서 4단계 수행** (별도 wakeup 필요 없음).
4. **분석 (phase="analyzing")**:
   - 4개 summary.txt 읽기.
   - 아래 "이미 알려진 이슈" 목록과 대조. **이미 알려진 실패는 재조사하지 않는다.** 새로 나타난 실패(전에 없던 TC, 또는 100% 재현되는 신규 패턴)만 로그 grep + spec 확인으로 원인 분석.
   - 사용자 승인(2026-10-06): **확실한 수정은 바로 적용, 애매하면 기록만 하고 넘어간다.**
     - "확실한 수정"의 기준: 오타/구식 VALUE(현재 FW 로그와 비교해 문자 그대로 불일치 확인됨), 타임아웃 실측 기반 부족, 액션 누락 등 — 근거가 로그로 명확히 입증되는 것.
     - "애매한 것"의 기준: 산발적/간헐적이라 재현율이 낮거나, FW 결함인지 하니스 문제인지 불확실하거나, 수정이 다른 TC에 영향을 줄 수 있는 것. → CHANGELOG에만 기록, spec/코드는 안 건드림.
     - 수정 전 반드시 `automation/backups/cycle<N>_<timestamp>/spec_v2.xlsx` (+필요시 harness/*.py)로 백업.
     - `automation/CHANGELOG.md`에 이번 사이클에서 발견한 것/적용한 것/적용 안 하고 넘어간 것을 전부 기록 (근거 로그 인용 포함).
     - openpyxl `cell.value=None`은 no-op이므로 지울 때 반드시 명시적으로 다시 대입할 것 (과거 실수 전적 있음).
   - phase="idle"로, `last_run_complete`=now, `cycle`+=1, `run_dirs`=null.
5. **phase == "idle"**이면 **바로 다음 사이클 시작** (완료 후 즉시 재시작 — 실행 자체가 보통 1~1.5시간 걸리므로 이게 자연스럽게 2시간 안팎 간격이 된다. "2h"를 추가로 기다리지 않는다. `interval_hours`는 참고용 메모일 뿐 대기 조건 아님):
   - `python run_all.py --source daemon`을 PowerShell `run_in_background: true`로 실행 (포그라운드로 돌리면 10분 타임아웃에 걸림).
   - `last_run_start`=now, `run_dirs`=현재 각 DUT 최신 로그 폴더 이름 기록, phase="running".
6. 다음 `ScheduleWakeup(delaySeconds: 3600, reason: "...")` 호출. `noop`은 CHANGELOG에 뭔가 추가했으면 false, 단순 대기면 true.

## 사용량 한도(rate-limit/usage) 대응
- 세션/위클리 사용량 퍼센트를 직접 조회하는 도구는 없음 — 선제적으로 "90%"를 감지할 방법이 없다. 대신 **아무 도구 호출(특히 Agent, PowerShell, ScheduleWakeup 등)이 rate-limit/usage/quota 관련 에러를 반환하면 그걸 신호로 삼는다.**
- 그런 에러를 만나면:
  1. 지금 하던 분석/수정 작업은 무리해서 계속하지 말고 중단.
  2. `automation/CHANGELOG.md`에 "사용량 한도로 인해 <시각>부터 일시 중지, <재개 예정 시각>에 재시도" 기록.
  3. `automation/state.json`에 진행 중이던 phase/cycle 상태를 그대로 저장해서 나중에 이어갈 수 있게 함.
  4. `ScheduleWakeup(delaySeconds: 3600, reason: "usage 한도로 인한 백오프 대기")`로 최소 1시간(연속으로 계속 막히면 그다음엔 재시도 없이 그냥 다음 정기 wakeup까지 기다림 — 무한 재시도 루프 방지) 쉬었다가 재개.
  5. 재개 시점에 다시 같은 에러가 나면 이 절차를 반복하되, 매번 CHANGELOG에 남겨서 나중에 사람이 패턴을 볼 수 있게 함.

## 절대 건드리면 안 되는 것 / 이미 알려진 이슈 (메모리 참고)
- TC030 (RTC ready timeout): 회귀 감시용, 정상 실패 상태. 수정 금지, known_issues.yaml에도 추가 금지.
- TC021/22/23/25 (네트워크 DNS): 자극 설계 보류 확정. 코드 수정 금지.
- FOTA 관련 TC 6개: 이미 spec에서 완전 삭제됨. 되살리지 말 것.
- TC032: BLOCKED_BY 처리됨(ADAS 통계 로그 자체가 FW에 없음). FW팀 확인 전까지 유지.
- TC102/TC109/TC152 (Driver ID): 자동화 불가 확정(2026-10-06). BLOCKED_BY 유지, 되살리지 말 것.
- **산발적 장비오류/간헐적 캡처 유실(TC001/006/002 등, 매번 다른 TC)**: 2026-10-06 확인 — `continuous_log.py`와의 포트 경합이 근본 원인일 가능성이 높음(위 "왜 daemon 모드" 참고). 이번 루프부터는 daemon 모드로 돌리므로 **이 범주 실패가 줄어드는지 자체가 이번 루프의 관찰 포인트**. 그래도 발생하면 "이미 알려진 것"으로 보고 재조사하지 말 것(단, 경합 때문이 아니라 재현 패턴이 바뀌었으면 새 이슈로 취급).
- **`ensure_login()` 로그인 오판 버그**: 2026-10-06 수정 완료(`harness/dut.py`, echo 마커 전송을 "로그 잠잠해진 뒤"로 지연시킴). 이번 루프에서 "로그인 거부됨" InfraError가 재발하는지가 관찰 포인트 — 재발하면 새 이슈로 취급해 재조사(수정이 불완전했다는 뜻이므로).
- TC209 (신규, 프레임드롭 감시): 2026-10-06 추가. 이번 루프에서도 정상 동작하는지 확인.

## 변경 이력
- 2026-10-06: 2시간 주기, daemon 모드 필수로 신규 루프 시작. 이전 추석 연휴 루프(6시간 주기, 9/23~9/29)와는 별개 — 그쪽 기록은 CHANGELOG.md 상단 cycle 0~21 항목 참고.
