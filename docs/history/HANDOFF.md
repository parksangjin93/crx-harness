# crx-harness 인수인계 문서 (2026-10-06 기준)

추석 연휴(9/23~9/28) 자동 회귀 + 이후 수동 작업(9/28~10/6) 내용 정리.
**지금 하려는 작업(TC 추가/수정)은 "7. 다음에 할 일" 참고.**

## 1. 이 프로젝트가 뭔지

`C:\Users\HW-RACK\Desktop\새 폴더\crx-harness` — CRX3212/TRX3208/VMC8PRO/XVM508 4대 DUT를
UART 로그 기반으로 자동 판정하는 pytest 하니스. `spec_v2.xlsx`가 TC 정의(ACTION/CHECK/ABSENT/
SHELL_CHECK)의 유일한 소스. `python run_all.py`로 4대 동시 실행
(`--source daemon` 옵션 추가됨 — crx-continuous 상시 프로그램 경유 접속, 아래 6번 참고).

## 2. 지금까지 고친 것 (코드/스펙 변경 누적, 최신순)

| 날짜 | 항목 | 내용 | 파일 |
|---|---|---|---|
| 10/1 | 타임아웃 19개 TC 전수 감사 | 363개 run 전수를 epoch 시각 대조로 감사 → 12개 TC 상향(TC001~028), TC007/027은 유실률 50~63%라 타임아웃 조정 보류하고 기능 이슈로 분류 | spec_v2.xlsx |
| 10/1 | **TIMEOUT_FROM 기준시각 오염 버그 수정** | 자체 ACTION 없는 TC(TC016/017/018)가 직전 TC의 액션완료 시각을 그대로 물려받아 시작하자마자 즉시 실패하던 구조적 버그. `if not tc.actions: dut.mark_action_done()` 추가로 수정 | harness/verdict.py |
| 10/1 | `boot_timeout_s` 180→300 | SOFT_CYCLE/ACC_CYCLE 내부 BOOT 대기(스펙 TIMEOUT과 별개)가 180s를 넘는 사례 재확인 | harness/dut.py |
| 10/1 | TC011/013/015 추가 상향 | 1차 상향(150/210/210)도 부족해서 재실측 후 210/270/420으로 재조정 | spec_v2.xlsx |
| 9/29 | `ensure_login()` Login-incorrect 오탐 수정 | 카메라 인증 로그가 인터리빙으로 태그 분리돼 `Login incorrect`만 단독으로 찍히는 걸 오판하던 버그 → `state=="PWD"`일 때만 카운트하도록 수정 | harness/dut.py |
| 9/29 | TC034 타임아웃 600→900 | 최초 발견 당시 76초 늦게 나오는 사례 확인 후 상향 (단, 10/1에 "타임아웃 문제 아닐 수도" 로 재평가됨 — 아래 3번 참고) | spec_v2.xlsx |
| 9/28 | TC030 xfail 처리 | 회귀 감시용 TC는 계속 실패하는 게 정상 → `regression_watch` 태그 + `pytest.mark.xfail(strict=True)`. 성공 시 rc에 영향 안 줌(summary에서도 "회귀 감시 중" 섹션으로 분리), FW 수정되면 strict 위반으로 역으로 눈에 띔 | spec_v2.xlsx, conftest.py |
| 9/28 | TC021/22/23/25 blocked | 네트워크 장애 재현 자극이 없어 계속 실패하던 것 → blocked 처리 | spec_v2.xlsx |
| 9/28 | **상세 검증 리포트 신설** | 매 실행마다 TC별 스텝 단위 근거를 `detail_report.md`로 자동 생성 (통과분도 "몇 초에 어떤 로그가 확인시켰는지" 남음) — **실제 장비로 검증 완료**(10/1 CRX3212 결과로 확인) | test_spec.py, harness/report.py |
| ~9/24 | `soft_cycle()` 버그 수정(2단계) | SHUTDOWN 미검출 시 바로 장비오류 처리하던 것 → 교차 확인 → 재부팅 완료 여부로 최종 판정 | harness/dut.py |
| ~9/23 | TC033 값 수정, TC032 blocked | 옛 FW 기준 값/존재하지 않는 로그 → 현재 FW 기준으로 교체·blocked | spec_v2.xlsx |

백업은 전부 `automation/backups/<timestamp>/` 에 있음. 사이클/조치별 상세 근거(왜 그렇게 판단했는지,
로그 인용 포함)는 전부 `docs/history/CHANGELOG.md`에 있음 — 의사결정 이유가 궁금하면 거기부터 볼 것.

## 3. 아직 안 고치고 남겨둔 진짜 이슈

1. **⚠️ trx3208 실제 커널 패닉 (2026-09-24)** — `forbidden` 패턴 검출기가 실제 커널 패닉+halt를
   잡음. FW(`8e06dd_260921`) 버그로 추정. **Jira 티켓화 검토 필요, 아직 안 함.**
2. **TC034, "미스터리"에서 "조건부 미발생"으로 재평가됨(10/1)** — 9/29엔 76초 늦게 나온 사례였는데
   10/1엔 900초 전체 구간 동안 아예 한 번도 안 나타남(다음 발생은 바로 다음 TC의 별개 리부팅
   사이클이었음). 디스크 사용량 임계치 등 조건부 로직일 가능성 — **타임아웃을 더 늘려도 해결
   안 될 수 있음, 잔여 이슈로 인정하고 보류 중.**
3. **TC007/TC027 기능 이슈 의심(10/1 신규 발견)** — MICOM_ACC_CHANGE(TC007), DSM statistics(TC027)
   로그가 각각 51%/63% 비율로 캡처 유실이 아니라 아예 미발생. 타임아웃 문제 아님, FW팀 공유 후보.
4. **⚠️ CRX3212 daemon 모드 Login-incorrect 1건(10/6, 신규)** — 비밀번호를 정상 전송("<비밀번호>" 에코
   확인됨)했는데 "Login incorrect" 응답. 같은 실행의 다른 모든 TC는 로그인 정상이라 비밀번호 자체
   문제는 아니고 일회성으로 보임. `--source daemon` 모드에서 처음 크게 돌린 실행이라 daemon 모드
   특유의 타이밍 문제일 가능성 — **근거 1건뿐이라 아직 코드 수정 안 함. 반복되는지 관찰 필요.**
5. **산발적 "장비오류" 패턴** — 매번 다른 TC에서 랜덤하게 발생, 랙/FW 전반의 신뢰성 문제로 결론.
   완전히 안 없어짐 — 계속 관찰 필요.
6. **TC102/TC109/TC152 (Driver ID 관련 3개)** — 블록 상태 유지 중. TC123 선례처럼 셸로 설정 파일
   직접 써서 RFID 카드 입력 흉내낼 수 있는지 라이브 탐색 필요, 아직 안 함.
7. **블록된 TC 23개("검증 조건 미정의, 로그 검증 가능성 있음") 재조사 완료 — 추가 후보 없음.**
   0건 확정, 6건 부분적, 9건 증거 없음, 8건 구조적 불가. 당분간 자동화 후보 목록에서 제외.

## 4. 손대면 안 되는 것 (원칙)

- **TC030**: 회귀 감시용. blocked/known_issues.yaml 넣지 말 것. xfail 처리로 이미 해결됨.
- **네트워크 4개(021/22/23/25)**: 자극 설계 전까지 blocked 유지.
- **FOTA 관련 TC 6개**: 이미 spec에서 완전 삭제됨. 되살리지 말 것.
- **TC77/95/131/186 등 UI-config 변환 검증 TC**: 셸로 직접 설정 바꾸면 검증 대상 자체가
  무의미해짐 — 자동화 불가 확정 상태 유지.
- **spec_v2.xlsx 일괄 수정 시 주의**: TC_ID만으로 매칭하면 같은 TC의 다른 스텝까지 잘못
  덮어쓸 수 있음(10/1에 실제로 한 번 겪고 되돌림) — 반드시 STEP 번호나 VALUE 텍스트까지 매칭할 것.
  편집 전엔 꼭 Excel에서 파일이 열려있지 않은지 확인할 것(저장 실패/충돌 이력 있음).

## 5. TC 추가 후보 (설계/검증 전)

- ~~`(encode) Encoding Handle Status for 60 seconds` 로그 — 프레임 드롭 감시용~~ **완료(2026-10-06,
  TC209로 반영)**. 상세는 `docs/history/CHANGELOG.md` "신규 TC209 추가" 항목 참고. 오프라인 재생
  검증 중 `conftest.py` offline 분기가 `--no-stimulus`를 반영 안 하는 기존 버그를 별도로 발견함
  (이번 작업 범위 아니라 수정 안 함, live 모드엔 영향 없음).
- ~~TC102/109/152 (Driver ID) — SHELL_CHECK로 ini 직접 조작 검토~~ **완료(2026-10-06, 자동화 불가로
  최종 확정)**. GitLab 코드 추적 결과 Driver ID는 RFID 카드 태그/QR 스캔이라는 순수 하드웨어 이벤트로만
  바뀌고, `SetDriverID()`는 런타임 메모리만 변경할 뿐 디스크 설정파일을 안 씀 — TC123과 달리 셸로
  흉내낼 파일 자체가 없음. 상세는 `docs/history/CHANGELOG.md` "Driver ID 라이브 탐색" 항목,
  `project_crx_harness_not_automatable.md` 참고. `spec_v2.xlsx` BLOCKED_BY 반영 완료.

## 6. `--source daemon` 모드 (신규, 2026-10)

`run_all.py --source daemon`으로 실행하면 포트를 직접 안 열고 **crx-continuous 상시 프로그램**에
접속해서 로그를 읽음(`harness/logsource.py`의 `DaemonLogSource`). 이미 반영된 관련 수정들:
- 단독 `\r`에서 줄이 쪼개져 시각 접두사를 잃어버리는 문제 수정 (`logsource.py`, newline="\n" 고정)
- InfraError 등 예외 메시지가 앞부분만 잘려서 실제 사유가 안 보이던 문제 수정 (`conftest.py`,
  reason을 뒤에서부터 2500자 유지)
- daemon 모드에서 다른 사람이 수동으로 포트 쓰고 있으면 skip 처리 (DaemonUnavailable)

**주의**: 이 작업들이 언제 누구에 의해 됐는지 이 대화 기록엔 없음(세션 바깥에서 진행된 것으로 보임,
메모리에도 기록 없었음) — `conftest.py`/`logsource.py`/`report.py`의 "2026-10 daemon 모드" 주석들을
직접 읽는 게 제일 정확함.

## 7. 다음에 할 일 (2026-10-06 작업 완료 후 갱신)

- ~~5번("TC 추가 후보") 우선순위 정하기~~ **완료**: TC209(프레임드롭 감시) 신규 추가.
- ~~6번(Driver ID) 라이브 탐색~~ **완료**: 자동화 불가로 확정.
- 새 TC 추가할 때 지켜야 할 것(이번에도 그대로 적용함): 실제 로그로 VALUE 검증 후 반영(TC123/TC166
  전례 — 옛 FW 기준 값을 그대로 썼다가 전부 오탐났던 적 있음), PRODUCT 스코프 정확히 지정, 추가 후
  `harness.spec.load()`로 재검증 필수. **TC209 작업에서 추가로 얻은 교훈**: ABSENT 스캔 구간이
  "마지막 CHECK가 매칭되는 그 순간까지"로 한정되므로, 헤더 줄 하나만 매칭하고 끝내면 그 뒤에 흐르는
  실제 데이터 줄이 통째로 감시 대상에서 빠질 수 있음 — 가짜 데이터를 주입해서 FAIL이 제대로 뜨는지
  양성 검증까지 반드시 할 것(오탐 없음만 확인하는 걸로는 부족).
- 다음 TC 추가 후보가 또 필요해지면 다시 운행 로그(uart.log)를 훑어서 "주기적으로 안정적으로 찍히는
  통계/상태 로그"부터 찾는 게 encode 로그 때처럼 효율적이었음.

## 8. 참고 위치

- 사이클/조치별 상세 기록: `docs/history/CHANGELOG.md`
- 코드/스펙 변경 백업: `automation/backups/<timestamp>/`
- 자동 루프 상태(참고용, 지금은 중단됨): `automation/state.json`, `docs/history/PLAYBOOK.md`
- Claude 메모리(자동 로드됨, 별도 조치 불필요): `project_crx_harness_overview.md`,
  `project_crx_harness_open_issues.md`, `project_crx_harness_not_automatable.md`,
  `reference_crx_harness_trackers.md`
