# 자동 회귀 루프 변경 이력

## Cycle 0 (준비, 2026-09-23 11:17)
사용자 요청: 추석 연휴(~9/28) 동안 6시간마다 자동 실행 + 분석 + 수정. 전부 자동 적용 승인, 무인 랙 운용 승인.

- 백업: `automation/backups/pre_cycle1_20260923_111717/spec_v2.xlsx`
- 적용한 수정 (직전 세션에서 조사한 TC032/TC033 후속):
  - **TC033**: CHECK VALUE `(bsd) ====== BSD Status for` → `BSD statistics.. ch`
    - 근거: 최신 런 uart.log(112k~118k줄, ACC_CYCLE 여러 회) 전수 검색 결과 과거 VALUE는 0건. 현재 FW(8e06dd_260921)는 `(dvr_app) BSD statistics.. chN total N, N.Nfps` 형태로 출력. 과거 "42만건 확인"은 다른(구) FW 빌드 기준으로 보임.
  - **TC032**: BLOCKED_BY 설정 (`FW 로그에 ADAS 주기 통계 로그 없음 - 스펙 재검토 필요`)
    - 근거: `(ADAS) ADAS statistics.. total` 패턴이 uart.log 전체에서 0건. ADAS 자체는 정상 init 성공(`dta_adas_initialize(0) succeed!!!`)하지만 DSM/BSD와 달리 이 FW는 ADAS의 주기 통계 로그를 아예 안 찍는 것으로 보임 — TIMEOUT 문제가 아니라 대체 지표가 없는 상태라 무기한 재시도해도 계속 실패했을 것. FW팀 확인 전까지 blocked.
- `harness.spec.load()`로 재로드 검증 완료 (TC032 blocked_by 설정됨, TC033 신규 VALUE 정상 반영).

## Cycle 1 (2026-09-23 11:19:15 ~ 12:40:00, 분석 12:42)
결과: crx3212 31P/6F/4S, trx3208 32P/6F/4S/1장비오류, vmc8pro 29P/8F/4S, xvm508 31P/7F/5S(blocked 1개 늘어남=TC032 확인).

이미 알려진 실패 (재조사 안 함): TC021/22/23/25(네트워크), TC030(RTC, 4대 전부), TC014/TC041(잔여 랜덤 미검출 baseline), TC034/TC039/TC012(BOOT 패턴 간헐적 미검출 - 기존 known-issue 카테고리), TC037(SHUTDOWN 패턴 간헐적 미검출 - 동일 카테고리).

**신규 발견 및 수정: TC123 "장비오류" (trx3208)**
- 증상: TC123(SOFT_CYCLE 액션)이 killall 후 `(LogWriter) SHUTDOWN` 로그를 90초 내 못 받아 `InfraError`로 하드 실패(장비오류) — TC 실패가 아니라 harness가 그 자리에서 죽는 수준.
- 원인: `harness/dut.py`의 `soft_cycle()`이 SHUTDOWN 로그 미검출 시 곧바로 InfraError를 던지는 구조였음. 이 세션 초반에 `acc_cycle()`에 적용했던 "SHUTDOWN/POWER-DOWN 로그 한 줄이 CP2108 공유로 간헐적으로 누락될 수 있으니 다음 로그로 교차 확인 후 관용 처리"하는 패턴이 `soft_cycle()`에는 없었음.
- 조치: `soft_cycle()`에 동일한 교차 확인 로직 추가 — SHUTDOWN 패턴이 안 걸리면 즉시 실패시키지 않고 다음 로그(`dvr_app is terminated`)로 실제 종료 여부를 재확인, 그것마저 없을 때만 InfraError. 백업: `automation/backups/cycle1_20260923_124219/dut.py`. `python -c "import harness.dut"`로 문법 검증 완료 (실제 장비 재현 검증은 다음 사이클 결과로 확인 예정).
- 참고: XVM508 쪽 TC037도 같은 카테고리(SHUTDOWN 패턴 미검출)지만 그쪽은 일반 CHECK 스텝 실패(장비오류 아님)라서 이번 수정과 무관 — 구조적으로 다른 코드 경로.

다음 사이클(cycle 2) 시작 예정: ~17:19 (6시간 주기).

## Cycle 2 (2026-09-23 17:20:32 ~ 18:42:33, 분석 직후)
결과: crx3212 32P/5F/4S, vmc8pro 31P/6F/4S, trx3208 36P/3F/4S(장비오류 없음!), xvm508 32P/6F/5S.

전부 이미 알려진 실패: TC021/22/23/25(네트워크), TC030(RTC, 4대 전부), TC015(vmc/xvm, 잔여 랜덤 미검출 baseline — 원래 %표에도 있던 항목).

**cycle 1에서 고친 `soft_cycle()` 교차 확인 패치 검증됨**: TC123이 이번엔 정상 통과, trx3208에 "장비오류" 자체가 안 나타남. 1회 관측이라 완전 확정은 아니지만 좋은 신호.

신규 버그 없음 — 이번 사이클은 수정 사항 없이 종료. 다음 사이클(cycle 3) 시작 예정: ~23:20.

## Cycle 3 (2026-09-23 23:21 ~ 2026-09-24 00:42, 분석 직후)
결과: crx3212 31P/6F/4S, trx3208 32P/6F/4S/1장비오류, vmc8pro 31P/6F/4S, xvm508 35P/3F/5S.

이미 알려진 실패: TC015(잔여 랜덤 baseline, crx), TC021/22/23/25(네트워크), TC030(RTC 4대), TC041(잔여 랜덤 baseline, trx), TC011 BOOT 미검출(vmc, 일반 CHECK 실패 - 장비오류 아님, known BOOT 미검출 카테고리), TC006 SHUTDOWN 미검출(xvm, 일반 CHECK 실패 - known SHUTDOWN 미검출 카테고리).

**신규는 아니지만 재발: TC011 "장비오류" (trx3208)**
- cycle 1에서 고친 `soft_cycle()`의 1차 교차 확인(SHUTDOWN → dvr_app is terminated)이 이번엔 **둘 다** 미검출로 다시 InfraError까지 갔음 (trace 확인: WARN 로그 두 번 다 찍히고 최종 InfraError).
- 1차 패치는 "한 줄 손실"만 커버했는데, 이번 사례는 두 로그가 동시에 안 잡힌 케이스라 부족했음.
- **추가 조치**: `soft_cycle()`에 최종 단계(재부팅 완료 BOOT 로그 + 로그인 성공) 확인을 한 겹 더 추가 — SHUTDOWN/terminated 로그가 둘 다 없어도, 실제로 재부팅+로그인까지 성공하면 정상 처리(로그 손실 vs 진짜 미종료를 최종적으로 재부팅 성공 여부로 가름). 진짜로 재부팅도 안 되면 그때 InfraError.
- 백업: `automation/backups/cycle3_20260924_004407/dut.py`. import 검증 완료. 다음 몇 사이클 결과로 재발 여부 계속 관찰 필요 — 완전히 안 없어질 수도 있음(근본 원인은 여전히 CP2108 공유 하드웨어 추정, 소프트웨어로는 관용 처리가 한계).

다음 사이클(cycle 4) 시작 예정: ~05:21.

## Cycle 4 (2026-09-24 05:22 ~ 06:44, 분석 직후) — ⚠️ 실제 FW 버그(커널 패닉) 발견, 코드 수정 없음
결과: crx3212 34P/3F/4S(개선), vmc8pro 31P/6F/4S, trx3208 33P/4F/4S/**2장비오류**, xvm508 31P/7F/5S.

**⚠️ trx3208에서 진짜 커널 패닉 발생 — 하니스 버그 아님, FW 버그로 추정. 코드 수정하지 않음.**
- TC003(DRV_Shutdown_Log) SOFT_CYCLE 도중, 그 다음 TC019 teardown에서 `forbidden` 패턴 검출기가 아래 4건을 실제로 잡음:
  ```
  [커널 메모리 접근 오류] Unable to handle kernel paging request
  [커널 스택 트레이스] Call trace:
  [커널 패닉]           Kernel panic - not syncing: Fatal exception
  [재부팅 실패 후 halt]  Reboot failed -- System halted
  ```
- 즉 dvr_app kill 직후 정상 재부팅 흐름 중간에 커널이 실제로 패닉→halt 됐고, 그 여파로 TC003·TC019 둘 다 soft_cycle()의 최종 재부팅 확인(cycle 3에서 추가한 fallback)까지 실패해서 장비오류(InfraError) 처리됨. cycle 3에서 추가한 "재부팅 완료로 최종 확인" 로직은 정상 동작한 것 — 로그 손실이 아니라 진짜 크래시였기 때문에 결국 InfraError로 올바르게 떨어짐(forbidden 검출기도 별도로 정확히 잡음, 서로 상충 없음).
- TC028(같은 run, trx3208)의 "SYSTEM_DRID 180s 미검출"은 이 크래시 여파로 재부팅이 평소보다 느려진 것으로 추정(같은 run 안에서 ACC_CYCLE로 실제 전원 재인가되면서 결국 복구됨 — TC030까지는 정상 도달).
- **이건 소프트웨어(하니스)로 "고칠" 문제가 아니라서 코드/스펙 변경 안 함.** 세션 전체에서 forbidden 패턴이 실제로 걸린 첫 사례로 보임 — 1회성 우발적 크래시인지 재현되는 FW 결함인지는 추가 관찰 필요. **연휴 끝나면 사람이 꼭 확인할 것: trx3208 FW(8e06dd_260921) 커널 패닉 건, Jira 티켓화 검토.**
- TC020(xvm508, 파일 close 180s 미검출)은 trx3208과 무관한 다른 보드 - 기존 잔여 랜덤 미검출 카테고리로 분류.

다음 사이클(cycle 5) 시작 예정: ~11:22. trx3208에서 같은 커널 패닉이 다시 나오는지 계속 지켜볼 것.

## Cycle 5 (2026-09-24 11:23 ~ 12:55, 분석 직후) — 코드 수정 없음, 관찰 결과만 기록
결과: crx3212 29P/7F/4S/**1장비오류**, trx3208 33P/5F/4S/**1장비오류**, vmc8pro 30P/7F/4S, xvm508 34P/4F/5S.

이미 알려진 실패: TC006(baseline), TC021/22/23/25(네트워크), TC030(RTC), TC041(baseline), TC013(baseline, xvm), TC028(post-ACC_CYCLE 로그 지연, 기존 카테고리), TC039(disk probe, 원래도 ~64% 대였던 TC — 신규 아님).

**⚠️ 장비오류 2건, 둘 다 코드 결함 아님으로 판단 — 수정 안 함:**
1. **crx3212 TC019**: `soft_cycle()`의 `_app_running()` 체크는 통과했는데, 바로 다음 `self.shell("killall -TERM dvr_app")` 명령이 30초간 응답 없음으로 InfraError. **캡처된 마지막 수신 로그가 커널 초기 SATA 드라이버 초기화 메시지(T+1.8~2.0s)** — 즉 명령을 보낸 시점에 장비가 이미 다시 부팅 중이었다는 뜻. TC018(ACC_CYCLE) 직후 TC019(SOFT_CYCLE)인데, `_app_running()`이 앱 동작을 확인한 바로 그 직후에 장비가 스스로 재부팅을 시작한 것으로 보임(우리 자극과 무관한 재부팅).
2. **trx3208 TC013**: SHUTDOWN/terminated/재부팅 완료(BOOT) 셋 다 시간 내 미검출 → InfraError. 이번엔 forbidden 패턴(커널 패닉 등)은 안 걸림 — 로그 손실인지 실제 무응답인지 구분 불가.

**중요 패턴**: **TC019이 벌써 두 사이클 연속 장비오류를 냄** (cycle 4: trx3208에서 실제 커널 패닉과 함께, cycle 5: crx3212에서 이번 증상). TC019 자체(SOFT_CYCLE, DRV_File_Close_RecInt)나 그 직전 TC018(ACC_CYCLE)과의 전환 구간에 뭔가 구조적으로 취약한 지점이 있을 가능성. 이건 하니스 코드의 "로그 손실 관용 처리" 문제가 아니라 **장비/FW가 우리 자극과 무관하게 예상치 못한 시점에 재부팅되는 현상**으로 보여서, 더 이상 소프트웨어 관용 로직을 추가하지 않기로 함(진짜 불안정을 감추는 꼴이 되기 때문). **연휴 끝나면 사람이 TC018→019 전환 구간 + trx3208 커널 패닉 건을 같이 묶어서 FW팀에 문의할 것을 추천.**

다음 사이클(cycle 6) 시작 예정: ~17:23. TC019 재발 여부 계속 추적.

## Cycle 6 (2026-09-24 17:24 ~ 18:45, 분석 직후) — 코드 수정 없음
결과: crx3212 30P/7F/4S, trx3208 34P/5F/4S(**TC019 재발 안 함**), vmc8pro 34P/3F/4S(가장 깨끗), xvm508 34P/3F/5S/**1장비오류**.

TC019은 이번엔 안 걸림(trx3208에서 정상 통과) — TC018→019 전환 자체가 구조적으로 고정 취약점은 아니라는 쪽에 무게. 이미 알려진 실패: TC013/TC020(crx, BOOT 미검출 baseline), TC021/22/23/25(네트워크), TC030(RTC).

**xvm508 새 장비오류: TC034 (ACC_CYCLE, SHUTDOWN 90s 미검출) + 직전 TC031도 실패(Loader Start 30s 미검출)**
- events.jsonl로 순서 확인: TC030(RTC 실패) → TC031(즉시 이어서 실패, ACC_CYCLE의 Loader Start 대기 30s 초과) → TC032(스킵) → TC034(장비오류, ACC_CYCLE의 SHUTDOWN 90s 초과). TC030 직후로 바짝 붙어서 실패가 연달아 남.
- 처음엔 "TC030 RTC 이슈 직후 몇 TC 동안 불안정해진다"는 가설을 세웠지만, **TC-ID 실행 순서상 TC030은 TC019보다 뒤에 옴** — 그런데 cycle 4/5의 TC018→019 장비오류는 TC030이 실행되기도 전에 일어난 일이라 이 가설과 안 맞음. 즉 "TC030 직후라서" 특정 인과관계가 있다기보다, **그냥 이 랙/FW가 하루 중 아무 때나 산발적으로 SHUTDOWN/Loader/BOOT 류 로그를 몇 초~몇십 초 늦게 찍거나 아예 재부팅이 불안정해지는 게 근본 성격**으로 보임 — cycle 4의 확인된 커널 패닉이 그 극단적인 사례고, 나머지는 정도가 약한 변주들.
- 코드 수정 안 함 — 이미 충분히 관용 처리가 들어가 있고, 더 늘리면 진짜 장애를 감추게 됨.

**누적 관찰(cycle 1~6)**: 장비오류/신규성 패턴 TC 목록 — TC123(c1, 고침), TC011(c3), TC003·TC019(c4, 커널패닉 확인), TC019·TC013(c5), TC031·TC034(c6). 매번 다른 TC라 특정 TC 결함이 아니라 전반적 신뢰성 이슈로 최종 판단. 연휴 후 사람 검토 시 이 목록 전체를 묶어서 FW팀에 전달 권장.

다음 사이클(cycle 7) 시작 예정: ~23:24.

## Off-cycle 런 (2026-09-24 20:12 ~ 21:32) — 자동 루프 밖에서 실행됨(사용자가 직접 확인차 돌린 것으로 추정), 간단 분류만
결과: crx3212 29P/8F/4S, trx3208 33P/5F/4S/1장비오류(TC001), vmc8pro 30P/6F/4S/1장비오류(TC037, 직전 TC036도 Loader Start 30s 미검출), xvm508 33P/5F/5S(가장 깨끗, known만).

새로 걸린 TC(TC009, TC001, TC036/TC037)도 전부 cycle 1~6에서 이미 확립한 "산발적 전반 신뢰성 이슈" 범주에 부합 — 코드 수정 없음. cycle 7 baseline은 이 런의 폴더 기준으로 잡음(원래 예정된 6시간 주기는 유지).

## Cycle 7 (2026-09-24 23:25 ~ 2026-09-25 00:45, 분석 직후) — 코드 수정 없음
결과: crx3212 32P/5F/4S(가장 깨끗), trx3208 32P/6F/4S/1장비오류(TC020), vmc8pro 30P/7F/4S, xvm508 32P/6F/5S.

전부 known 카테고리: TC021/22/23/25(네트워크), TC030(RTC), TC015/TC041(baseline), TC020(trx에서 장비오류, xvm에서는 일반 BOOT 미검출 — 누적 목록에 추가, 여전히 "매번 다른 TC" 패턴과 일치). 새로운 원인 없음, 추가 조치 없음.

다음 사이클(cycle 8) 시작 예정: ~05:25.

## Cycle 8 (2026-09-25 05:26 ~ 06:44, 분석 직후) — 코드 수정 없음, 가장 깨끗한 사이클
결과: crx3212 32P/5F/4S, trx3208 33P/6F/4S(장비오류 없음), vmc8pro 31P/6F/4S, xvm508 36P/2F/5S(최고 기록).

전부 known: TC021/22/23/25(네트워크), TC030(RTC), TC002(trx, BOOT 180s 미검출 — baseline 카테고리), TC014(vmc, 동일 카테고리). 장비오류 0건 — cycle 1~8 중 가장 깨끗함.

부가 관찰(기능적 문제 아님): trx3208 요약에 "FW ?"로 버전이 안 찍힘, xvm508 요약엔 FW 문자열 끝에 커널 dmesg 타임스탬프가 섞여 들어감(`1.1.0.RC2.A4[   36...`) — 둘 다 이미 알려진 "커널 dmesg와 앱 로그 라인이 섞이는" 캡처 노이즈의 변주로 보임, report.py의 FW버전 표시에만 영향, TC 판정 로직과는 무관. 코드 수정 안 함.

다음 사이클(cycle 9) 시작 예정: ~11:26.

## Cycle 9 (2026-09-25 11:27 ~ 12:48, 분석 직후) — 코드 수정 없음, 장비오류 0건
결과: crx3212 31P/6F/4S, trx3208 33P/6F/4S, vmc8pro 31P/6F/4S, xvm508 33P/5F/5S. 4개 보드 전부 장비오류 없음 — cycle 8에 이어 2연속 클린.

전부 known: TC021/22/23/25(네트워크), TC030(RTC), TC020/TC027/TC004(각기 다른 보드, 짧은 타임아웃 내 로그 미검출 - 기존 baseline 카테고리와 동일 성격). 새로운 원인 없음.

다음 사이클(cycle 10) 시작 예정: ~17:27.

## Cycle 10 (2026-09-25 17:29 ~ 18:53, 분석 직후) — 코드 수정 없음, 장비오류 0건 (3연속 클린)
결과: crx3212 29P/8F/4S, trx3208 33P/6F/4S, vmc8pro 31P/6F/4S, xvm508 34P/4F/5S. 4개 보드 전부 장비오류 없음 — cycle 8,9,10 3연속.

전부 known: TC021/22/23/25(네트워크), TC030(RTC), TC006/TC020/TC039(crx, baseline 카테고리), TC015(trx, baseline), TC008(vmc, baseline), TC013/TC014(xvm, baseline). 새로운 원인 없음.

## 연휴 후 수동 조치 2 (2026-09-28 11:57, 사용자 복귀 후)
- 백업: `automation/backups/manual_20260928_115719/conftest.py`
- **TC030을 `pytest.mark.xfail(strict=True)`로 표시하도록 `conftest.py`에 `pytest_collection_modifyitems` 훅 추가.** `case.tags`에 `regression_watch`가 있는 TC를 자동으로 xfail 처리.
  - 이유: 사용자가 report.py의 "회귀 감시 중" 섹션 분리로도 만족 못 하고 "그냥 성공을 보고 싶다"고 요청. TC030을 BLOCKED_BY로 완전히 빼는 건 FW 수정 감지 기능을 잃는 트레이드오프가 있어서, 대신 pytest 표준 관용구인 xfail(strict=True) 적용.
  - 동작: 예상대로 계속 실패하면 XFAIL로 집계되어 pytest 종료 코드(rc)에 영향 안 줌 → `run_all.py`가 rc==0이면 "성공"으로 표시하므로, TC030 외에 다른 실패가 없는 사이클은 이제 "성공"으로 뜸. 반대로 TC030이 실제로 통과해버리면(FW 수정됨) strict=True 때문에 그 자체가 실패로 뜨어 감지 기능은 유지됨.
  - `import conftest`로 문법 검증 완료. 실제 장비 재현 검증은 다음 수동 실행 결과로 확인 필요.
- 사용자 요청으로 병행 진행한 별도 조사(에이전트, 백그라운드): BLOCKED_BY="검증 조건 미정의 (로그 검증 가능성 있음)"인 23개 TC 전수를 20사이클치 누적 uart.log(4개 DUT, 총 300+ 런)와 대조해서 자동화 가능 여부 재평가. 결과: **0건 확정(CONFIRMED), 6건 부분적(PARTIAL), 9건 증거없음, 8건 로그로 검증 불가**로 결론. 상세:
  - PARTIAL 6건(TC073,079,089,124,151,170): 관련 로그는 찾았으나 발생률이 낮거나(TC124: ~80런 중 6회), 정상/비정상을 구분할 결정적 신호가 없거나(TC073: 정상 반복과 병리적 반복을 구분 못함), 현재 CHECK/ABSENT DSL로 표현 불가(빈도/카운트 기반 판정 필요).
  - NO EVIDENCE 9건(TC048,056,058,065,118,138,147,169,190): 관련 문자열이 전체 코퍼스에서 0건 검출(예: "qmicli", "QMI protocol error", "img prefix is not matching" 등 전혀 안 찍힘).
  - NOT VERIFIABLE 8건(TC087,091[중복아님-102],109,111,152,165,197 등): 물리적 조작(BSD 실제 감지 이벤트, Driver ID 카드 입력, FW 이미지 배치, FACTORY DEFAULT 버튼)이 전제조건인데 하니스의 ACTION이 ACC_CYCLE/SOFT_CYCLE 둘뿐이라 트리거 자체가 불가능. 단, TC102/109/152(Driver ID 관련)는 TC123 선례처럼 SHELL_CHECK로 driverid.ini를 직접 써서 시뮬레이션하는 방법이 있을 수 있어 후속 논의 가치 있음.
  - **결론: 이번엔 spec_v2.xlsx에 추가할 만한 확실한 후보가 없었음.** 억지로 추가하면 TC166/TC123 초반에 겪었던 "틀린 VALUE로 오탐" 문제를 반복할 위험이 큼. spec_v2.xlsx 변경 없음.

## 연휴 후 수동 조치 3 (2026-09-29 14:26) — CRX3212 FW 1.3.0.RC3(FOTA 후) 첫 실행, 실제 버그 발견+수정
사용자가 직접 `python run_all.py` 실행 (CRX3212/VMC8PRO는 그 사이 FOTA로 FW 갱신됨: CRX3212 1.3.0.RC3, VMC8PRO 1.3.0.RC4 — 이 하니스로는 처음 보는 FW 버전).

- `detail_report.md` 기능이 실제 장비에서 정상 동작 확인됨 (CRX3212 결과로 검증) — 통과 TC마다 몇 초에 어떤 로그 줄이 확인시켰는지 전부 남음.
- xfail(strict) 처리도 실제로 확인됨: pytest 최종 줄에 `3 failed, 29 passed, 8 skipped, 1 xfailed`로 TC030이 분리 집계됨. summary.txt에서도 실패 목록에서 완전히 빠짐(스킵 카운트에 흡수) — 다만 이 때문에 "회귀 감시 중" 섹션도 이제 안 뜨는 부작용 있음(아래 참고).
- **실제 버그 발견+수정: `ensure_login()`의 "Login incorrect" 오탐 (crx3212 TC009, TC014 재발)**
  - 증상: 두 TC 모두 `harness.dut.InfraError: 로그인 거부됨 (2회)`로 장비오류 처리. 직전 수신 로그가 전부 IP 카메라 ONVIF 인증 시도 로그(`(dvr_app) uri:->.../onvif/...`, `username:->admin`, `password:->admin`)였고, 그 직후 태그 없는 `Login incorrect` 한 줄이 단독으로 찍혀서 카운트됨.
  - 원인: `harness/dut.py`의 `ensure_login()`이 "Login incorrect" 문자열을 **state와 무관하게** 무조건 카운트하고 있었음. 2026-09-17에 이미 한 번 같은 증상(TC009)을 겪어서 `SRC_RE` 태그 필터를 넣어뒀는데, 이번엔 그 "Login incorrect" 줄 자체가 인터리빙으로 태그 접두사와 분리돼 필터를 피해감 — 기존 수정이 불완전했음.
  - 수정: `if "Login incorrect" in t:` → `if state == "PWD" and "Login incorrect" in t:` — 우리가 실제로 방금 비밀번호를 보낸 직후(PWD 상태)일 때만 카운트하도록 상태 기반으로 한 겹 더 걸렀다. 태그 유무에 의존하지 않아서 인터리빙에 안 흔들림. `import harness.dut`로 문법 검증 완료. 백업: `automation/backups/manual_20260929_142610/dut.py`. 실제 재현 검증은 다음 실행 결과로 확인 필요.
  - TC020(같은 run, "BOOT 180s 미검출")은 별개, 기존 baseline 카테고리.
- **후속 확인 필요**: xfail 처리로 TC030이 "skipped" outcome으로 넘어가면서 report.py의 "회귀 감시 중" 섹션 분리 로직이 더 이상 안 걸림(fails 리스트에 애초에 안 들어옴). 기능상 문제는 아니지만(사용자가 원한 "성공으로 보이기"는 오히려 더 잘 달성됨), detail_report.md의 TC030 헤더가 "SKIP"으로 뜨는 건 실제로는 실행되고 실패했다는 사실과 안 맞아 약간 혼란스러움 — 나중에 정리 여지 있음.

**최종 결과 (4개 전부 완료):**
| DUT | FW | 결과 | 비고 |
|---|---|---|---|
| CRX3212 | 1.3.0.RC3 (FOTA 신규) | 29P/3F/8S/1xfail | TC009·TC014 Login incorrect 버그 발견+수정, TC020 known |
| VMC8PRO | 1.3.0.RC4 (FOTA 신규) | 32P/0F/9S | 완전 클린 |
| TRX3208 | 1.3.0.RC4 (FOTA 신규) | 32P/2F/9S | TC020 known, TC034 기존 미스터리 재발(cycle 11과 동일 계열) |
| XVM508 | 1.1.0.RC2.A4 (변경 없음) | 32P/1F/9S/1xfail(+1장비오류 TC011) | soft_cycle() SHUTDOWN/BOOT 이중 미검출 — 기존 "산발적 신뢰성 이슈" 카테고리, forbidden 패턴 없음(커널 패닉 아님) |

4개 DUT 전부에서 `1 xfailed`로 TC030이 정상적으로 분리 집계됨 - xfail 기능 전 기종에서 확인 완료. `detail_report.md`는 CRX3212 기준으로 확인, 나머지도 동일 구조로 생성됨.

## 연휴 후 수동 조치 4 (2026-09-29 14:41) — TC034 "미스터리" 정정 및 해결
사용자가 "TC020,34가 아직도 FAIL"이라고 재질문 — TC034를 events.jsonl의 정확한 epoch 타임스탬프로 다시 대조.

- **cycle 11의 "TC034 미스터리"는 틀린 결론이었음, 정정함.** 이번(TRX3208, FW 1.3.0.RC4) 실행에서 TC034의 ACC_CYCLE 완료 시각(13:59:27.82) + 600s = 14:09:27.82 마감인데, 실제 `(IntDelete) DISK(INT) START OVERWRITE INIT!` 로그는 **14:10:44에 딱 76초 늦게** 찍힘 (uart.log 30건 전수 검색 + events.jsonl의 tc_start/action done 타임스탬프를 epoch→wall-clock 변환해서 정밀 대조, 이번엔 grep 줄번호 근접이 아니라 정확한 시각으로 확인함).
- cycle 11 당시 "로그엔 있는데 미검출 처리됐다"고 봤던 근거는 `failures/TC034.log` 슬라이스였는데, `_slice_failures()`가 이런 긴 타임아웃 실패에서 판정 시각을 못 잡으면 최근 400줄/1줄만 덤프하는 fallback 로직이 있어(오늘도 TC034.log가 1줄만 남음), 실제로는 다른 TC 구간의 성공 로그를 잘못 보고 "여기 있네"로 착각했을 가능성이 높음.
- **결론: 코드 버그 아니고, 그냥 이번엔 재부팅이 76초 더 걸린 것.** TC032/033처럼 "값 자체가 틀림"이 아니라 TC008/023 등처럼 "가끔 느려서 타임아웃 부족"인 케이스 — 근거가 명확해서 TIMEOUT 600→900s로 상향 조정함 (spec_v2.xlsx, row 78). `harness.spec.load()`로 검증 완료. 백업: `automation/backups/manual_20260929_144135/spec_v2.xlsx`.
- **부수 발견(별도 이슈, 아직 미수정)**: `harness/report.py`의 `_slice_failures()`가 긴 타임아웃 실패(600s+) 케이스에서 실패 로그 슬라이스를 제대로 못 잘라내는 경우가 있음(1줄만 남거나 엉뚱한 구간). `tc_steps` 이벤트가 이제 있으니 우선순위는 낮음(재현/원인 파악에 detail_report.md로 대체 가능) — 다만 failures/*.log 자체의 신뢰성 문제라 나중에 손볼 가치 있음.
- TC020은 여전히 하드웨어 잔여 노이즈(공유 USB-시리얼 브리지) 카테고리로 재확인 - 소프트웨어로 근본 해결 어려움, 조치 없음.

## 연휴 후 수동 조치 5 (2026-09-30) — 120s/180s 타임아웃 19개 TC 전수 감사 + 구조적 버그 발견/수정
사용자 요청으로 120/180s 쓰는 19개 TC를 363개 run 전수(2026-09-08~09-30) 대상 epoch 시각 대조 방식(TC034 때와 동일 방법론)으로 에이전트 감사. 결과를 직접 일부 검증(TC016 사례 events.jsonl 확인) 후 반영.

**중요 발견: TIMEOUT_FROM=ACTION 기준 시각 오염 버그 (TC016/017/018)**
- 증상: 이 3개 TC는 자체 ACTION이 없는데, `dut.last_action_t`(TIMEOUT_FROM=ACTION 기준 시각)가 리셋 안 되고 **직전 TC의 액션 완료 시각을 그대로 물려받음** — 심지어 그 직전 TC(TC013→014→015)가 자기 타임아웃 예산을 다 써버린 뒤였다면, TC016이 시작되는 순간 이미 데드라인이 지나있어 **폴링 시작도 못 하고 즉시(0.5초 만에) FAIL**.
- 직접 확인: `CRX3212\2026-09-08-16시47분09초\events.jsonl` — TC016의 `tc_start`(982.19s)~`step_done`(982.69s) 사이 0.5초 만에 `elapsed: 121.1`로 실패 처리됨. TC016 자신은 ACTION을 실행한 적이 없음(TC013/014/015만 SOFT_CYCLE 실행).
- 수정: `harness/verdict.py`의 `run_tc()`에 `if not tc.actions: dut.mark_action_done()` 추가 — `no_stimulus`(관찰모드) 분기에는 이미 있던 동일 패턴을 실제 자극 분기에도 적용. ACTION 없는 TC는 이제 자기 시작 시각을 기준으로 새로 잡음. `import harness.verdict`로 검증 완료. 백업: `automation/backups/manual_20260930_133410/verdict.py`.
- 이 구조는 TC013→020, TC026→028 등 다른 체인에도 잠재적으로 영향 줄 수 있으나(부가 발견), 이번 수정이 ACTION 없는 모든 TC에 일괄 적용되므로 별도 개별 대응 불필요.

**타임아웃 상향 (12개 TC, spec_v2.xlsx, 근거: 실제 지각 완료 확인, 유실 아님):**
TC001(180→300), TC002(180→270), TC006(120→300), TC009(180→270, 표본1 약함), TC011(120→150), TC012(180→330, 표본4 약함), TC013(120→210, step2만), TC014(120→210, step2만), TC015(120→210, step2만), TC019(180→210), TC020(180→400), TC028(180→300).

**⚠️ 적용 중 실수 발견/즉시 수정**: 첫 스크립트가 TC_ID만 매칭하고 스텝 번호를 안 가려서 TC001(step1~3, 원래 None/10/15초)과 TC013/014/015(step1, RECORD ON, 원래 30초)까지 같이 300/210으로 잘못 덮어씀. `harness.spec.load()`로 스텝별 값 재확인 중 발견, 즉시 원래 값으로 되돌리고 올바른 스텝(step4, step2)만 다시 확인 완료. **앞으로 TC_ID 기준 일괄 수정 시 반드시 STEP 번호까지 매칭할 것** (교훈으로 기록).

**타임아웃 조정 보류, 별도 기능 이슈로 의심 (조사 필요, FW팀 공유 권장):**
- **TC007** (MICOM_INFO/MICOM_ACC_CHANGE): 88건 조사 중 45건(51%)이 액션 완료 후 파일 끝까지 로그 자체가 전혀 없음(유실 아니라 진짜 미발생 의심). 단순 캡처 노이즈치고 비율이 너무 높음 — MICOM_ACC_CHANGE 이벤트가 특정 조건에서 아예 발생 안 하는 것일 수 있음.
- **TC027** (DSM statistics): 19건 중 12건(63%)이 동일 패턴(유실 아니라 DSM 서브시스템 미기동 의심). 정상 시 ~60초 주기로 계속 찍히는 로그인데 이 정도 비율로 전혀 안 나오는 건 캡처 문제로 보기 어려움.
- 둘 다 타임아웃을 늘려도 대부분 해결 안 됨 — 근본 원인 규명이 우선.

**TC 추가 후보 (신규 발견, 검토 가치 있음):**
- `(encode) Encoding Handle Status for 60 seconds` 로그가 60초 주기로 안정적으로 찍히며 채널별 vloss(프레임 드롭)/fps/bitrate 통계를 포함함 — 채널별 프레임 드롭 감시용 신규 TC 후보로 적합해 보임. 아직 스펙 설계/검증 전.

**손 안 댐 (증거 없음/부족):** TC010, TC113 — 감사 기간 동안 CHECK 타임아웃 실패 자체가 0건(전부 다른 유형인 "자극 실패"였음), 조정할 근거 없음.

## 연휴 후 수동 조치 6 (2026-10-01) — 어제 올린 타임아웃 일부 재실패, 정밀 재분석
새 FW(CRX3212/VMC8PRO/TRX3208 1.3.0.RC5, XVM508 3a60fa_260929_1)로 다시 실행한 결과 TC011/013/015/034가 또 실패 — events.jsonl epoch 타임스탬프로 재대조.

- **TC011(150→210 예정이었으나 실측 후 210 확정)**: UNMOUNT(SD)가 주기적(60~90초 간격)으로 찍히는 로그였음. 데드라인(150s)을 9.5초 차로 놓침 — 근소한 미스. 210s로 재상향.
- **TC013 step2(210→270)**: RECORD OFF(SD)도 동일 주기 로그, 데드라인을 13초 차로 놓침. 270s로 재상향.
- **TC015 step1(210→420)**: RECORD ON(HDD)을 218초 차로 크게 놓침(이전 감사의 max 348.6s와 일치하는 큰 변동성). 420s로 상향.
- **TC034(900s) — ⚠️ 재실패했지만 타임아웃을 더 늘리지 않기로 함.** 이번엔 "늦게 나옴"이 아니라 **TC034 자신의 ACC_CYCLE 완료 후 900초 전체 구간 동안 해당 로그가 통째로 안 나옴** — 다음 발생은 TC035(바로 다음 TC)의 완전히 별개인 새 리부팅 사이클에 속함(TC034 창과는 무관). 즉 이번엔 "유실"이 아니라 **이 사이클 자체에서 IntDelete 오버라이트 초기화가 애초에 발생하지 않은 것으로 보임** — 디스크 상태(사용량 임계치 등)에 따라 조건부로만 도는 로직일 가능성. 타임아웃을 아무리 늘려도 이런 유형의 미스는 해결 안 됨. 900s 유지, "완전 해결 불가, 잔여 이슈로 인정" 쪽으로 재분류.
- **TC018/TC019 (VMC8PRO) 신규 유형 확인**: 둘 다 spec-level CHECK 타임아웃이 아니라, SOFT_CYCLE/ACC_CYCLE 액션 자체의 내부 `boot_timeout_s`(하드코딩 180s, `harness/dut.py` DutConfig 기본값)를 못 맞춰서 "자극 실패"로 떨어짐. 이건 세션 초반부터 미답으로 남아있던 질문("boot_timeout_s도 올릴지")이었는데, 오늘 실측으로 재부팅이 180s를 넘는 사례가 실제로 또 나와서 **TC001의 BOOT 체크(180→300, 수동조치5)와 동일 근거로 `boot_timeout_s` 기본값도 180→300s로 상향**. 백업: `automation/backups/manual_20261001_110414/dut.py`.
- **참고(미해결 의문)**: 어제(9/30) 분명히 `harness.spec.load()`로 TC015 step1=30s(미변경 확인)까지 재검증했는데, 오늘 08:25 런의 실제 이벤트 로그엔 step1 timeout이 210s로 찍혀 있었음(원래 step1은 절대 210으로 바뀐 적이 없어야 함 - 9/30에 실수로 한 번 210이 됐다가 바로 되돌린 값과 공교롭게 일치). 지금(11시) 확인한 파일은 다시 30이었음. 정확한 원인(수동 편집/저장 타이밍 등) 특정 못 함 - **spec_v2.xlsx를 엑셀로 열어두는 동안 변경사항이 있었다면 그게 원인일 가능성 높음.** 지금은 올바른 값(420)으로 재확정했고, 앞으로 이런 불일치 발견되면 바로 알려달라고 안내함.

다음 사이클(cycle 11) 시작 예정: ~23:29.

## Cycle 11 (2026-09-25 23:29 ~ 2026-09-26 01:00, 분석 직후) — 코드 수정 없음, 장비오류 0건 (4연속 클린)
결과: crx3212 32P/5F/4S, trx3208 33P/6F/4S, vmc8pro 31P/6F/4S, xvm508 32P/6F/5S. 4연속 장비오류 0건.

기본 known: TC021/22/23/25(네트워크), TC030(RTC), TC004(baseline). **TC034이 trx3208·xvm508 두 보드에서 동시에 "600s 내 미검출"** — 좀 더 파봤음.

- TC034 = ACC_CYCLE 후 CHECK `(IntDelete) DISK(INT) START OVERWRITE INIT!` (TIMEOUT_FROM=ACTION, 600s).
- 두 보드 실패 로그(failures/TC034.log) 모두에서 이 문자열이 **실제로 존재함을 확인**(trx3208: T+43.7s, xvm508: T+38.3s, 둘 다 `(LogWriter) BOOT`보다도 먼저 찍힘). 하니스 구조상 `action_buf`가 자극(ACC_CYCLE) 구간 로그를 보관해뒀다가 CHECK가 먼저 그 버퍼부터 훑고(verdict.py `_await_one`), 그다음에야 라이브 로그를 기다리는 구조라 이론적으로는 놓칠 이유가 없어 보임 — 버퍼 크기(4000줄, 초과시 앞 1000줄 폐기)도 이 구간 로그량(로그인까지 ~400줄 수준)을 훨씬 웃돎.
- 즉 **"왜 실패했는지 코드 로직만 봐서는 설명이 안 됨** — 추측성 코드 수정은 하지 않음. 실제 라이브 실행 중 `action_buf`/`_recording` 상태를 찍어보는 등 별도 디버깅이 필요한 사안으로 남겨둠. 연휴 후 사람이 살펴볼 것 권장(단순 known-issue로 묻지 말고 재현 스크립트로 따로 볼 가치 있음).

다음 사이클(cycle 12) 시작 예정: ~05:30.

## Cycle 12 (2026-09-26 05:31 ~ 06:51, 분석 직후) — 코드 수정 없음, 장비오류 0건 (5연속 클린)
결과: crx3212 32P/5F/4S, trx3208 33P/6F/4S, vmc8pro 33P/4F/4S, xvm508 33P/5F/5S. 5연속 장비오류 0건.

전부 known: TC021/22/23/25(네트워크), TC030(RTC), TC041(baseline). 새로운 원인 없음.

다음 사이클(cycle 13) 시작 예정: ~11:31.

## Cycle 13 (2026-09-26 11:32 ~ 12:53, 분석 직후) — 코드 수정 없음, 5연속 클린 종료
결과: crx3212 32P/5F/4S, trx3208 32P/6F/4S/1장비오류(TC015), vmc8pro 29P/8F/4S, xvm508 34P/3F/5S/1장비오류(TC020).

TC015·TC020 둘 다 이미 누적 목록에 있던 TC(TC015: baseline 잔여미검출, TC020: cycle 7에서도 장비오류였음) — 새 패턴 아님, 기존 "산발적 전반 신뢰성 이슈" 결론 유지. 나머지도 전부 known: TC021/22/23/25(네트워크), TC030(RTC), TC004/TC006/TC027/TC028(baseline 카테고리). 코드 수정 없음.

다음 사이클(cycle 14) 시작 예정: ~17:32.

## Cycle 14 (2026-09-26 17:34 ~ 18:56, 분석 직후) — 코드 수정 없음, 장비오류 0건
결과: crx3212 32P/5F/4S, trx3208 32P/7F/4S, vmc8pro 30P/7F/4S, xvm508 32P/6F/5S. 장비오류 없음.

전부 known: TC021/22/23/25(네트워크), TC030(RTC), TC004/TC039/TC006/TC028/TC041(각기 다른 보드, 기존 baseline 카테고리). 새로운 원인 없음.

다음 사이클(cycle 15) 시작 예정: ~23:34.

## Cycle 15 (2026-09-26 23:35 ~ 2026-09-27 00:57, 분석 직후) — 코드 수정 없음, 장비오류 0건
결과: crx3212 30P/7F/4S, trx3208 34P/5F/4S(가장 깨끗), vmc8pro 29P/8F/4S, xvm508 32P/6F/5S. 장비오류 없음.

전부 known: TC021/22/23/25(네트워크), TC030(RTC), TC013/TC028(crx, BOOT미검출 baseline), TC006/TC011/TC020(vmc, 같은 카테고리 3건 겹침 - 그래도 known), TC041(baseline). 새로운 원인 없음.

다음 사이클(cycle 16) 시작 예정: ~05:35.

## Cycle 16 (2026-09-27 05:37 ~ 06:57, 분석 직후) — 코드 수정 없음, 장비오류 0건
결과: crx3212 31P/6F/4S, trx3208 33P/6F/4S, vmc8pro 29P/8F/4S, xvm508 36P/2F/5S(최고 기록). 장비오류 없음.

전부 known: TC021/22/23/25(네트워크), TC030(RTC), TC039(baseline), TC041(baseline), TC007/TC013/TC027(vmc, BOOT미검출 baseline 3건 겹침). 새로운 원인 없음.

다음 사이클(cycle 17) 시작 예정: ~11:37.

## Cycle 17 (2026-09-27 11:38 ~ 12:59, 분석 직후) — 코드 수정 없음
결과: crx3212 31P/6F/4S, trx3208 34P/5F/4S(가장 깨끗), vmc8pro 31P/6F/4S, xvm508 32P/5F/5S/1장비오류(TC014).

TC014 장비오류는 기존 "산발적 전반 신뢰성 이슈" 패턴과 동일 성격(누적 목록에 추가). 나머지 전부 known: TC021/22/23/25(네트워크), TC030(RTC), TC015/TC028(baseline). 새로운 원인 없음.

다음 사이클(cycle 18) 시작 예정: ~17:38.

## Cycle 18 (2026-09-27 17:39 ~ 19:03, 분석 직후) — 코드 수정 없음, 장비오류 0건
결과: crx3212 30P/7F/4S, trx3208 33P/6F/4S, vmc8pro 30P/7F/4S, xvm508 31P/7F/5S. 장비오류 없음.

전부 known: TC021/22/23/25(네트워크), TC030(RTC), TC039/TC006/TC041/TC012/TC013(baseline). TC166이 crx3212·vmc8pro 두 보드에서 "BOOT 180s 미검출"로 실패했는데, 이번 세션에서 고쳤던 WD 레지스트리 값(WD:\d+) CHECK 자체가 아니라 그 앞의 재부팅 자체가 늦어진 것 — 기존 BOOT미검출 baseline 카테고리와 동일, 회귀 아님. 새로운 원인 없음.

다음 사이클(cycle 19) 시작 예정: ~23:39.

## Cycle 19 (2026-09-27 23:40 ~ 2026-09-28 01:00, 분석 직후) — 코드 수정 없음
결과: crx3212 30P/7F/4S, trx3208 33P/6F/4S(가장 깨끗), vmc8pro 30P/5F/4S/**2장비오류**(TC002,TC013), xvm508 33P/5F/5S.

vmc8pro가 이번 사이클 조금 거칠었음(TC001 Loader Start 30s 미검출, TC002·TC013 장비오류, FW 버전도 cycle 8때처럼 "FW ?"로 안 찍힘) — 전부 기존에 확인한 "산발적 전반 신뢰성 이슈" + "커널dmesg/앱로그 인터리빙으로 FW버전 파싱 실패" 카테고리와 동일. 새 원인 아님. 나머지 known: TC021/22/23/25(네트워크), TC030(RTC), TC006/TC007/TC037(baseline).

**종료일이 가까워짐(2026-09-29 00:00)** — 다음 사이클(cycle 20, ~05:40)부터 남은 사이클 수를 계속 체크할 것.

다음 사이클(cycle 20) 시작 예정: ~05:40.

## Cycle 20 (2026-09-28 05:41 ~ 07:02, 분석 직후) — 코드 수정 없음, 장비오류 0건
결과: crx3212 32P/5F/4S, trx3208 33P/6F/4S, vmc8pro 29P/8F/4S, xvm508 33P/5F/5S. 장비오류 없음.

전부 known: TC021/22/23/25(네트워크), TC030(RTC), TC006/TC004/TC028/TC036(baseline). 새로운 원인 없음.

다음 사이클(cycle 21) 시작 예정: ~11:41. (남은 사이클: 이번 포함 앞으로 최대 3~4회, 종료일 2026-09-29 00:00)

## 연휴 후 수동 조치 (2026-09-28 09:17, 사용자 복귀 후 자동 루프 중단하고 직접 진행)
- 백업: `automation/backups/manual_20260928_091759/spec_v2.xlsx`
- TC021/22/23/25(네트워크 장애 재현 4개 TC) BLOCKED_BY 처리 — "자극 설계 미완료 (네트워크 장애 재현 수단 없음, 보류 확정)". 이유: 회귀 감시 목적(TC030)과 성격이 달라 known_issues.yaml 필터링 대상이 아니고, 자극 자체가 없어 계속 타임아웃만 나던 상태라 BLOCKED_BY로 실행 자체를 스킵.
- TC030(RTC 회귀 감시)은 의도적으로 손대지 않음 — known_issues.yaml에도, spec의 BLOCKED_BY에도 넣지 않음. FW팀이 고치면 자동으로 통과로 바뀌어야 하므로 계속 실행·실패 상태 유지.
- `harness.spec.load()`로 재검증 완료.

## 신규 TC209 추가: DRV_Encoding_FrameLoss_Monitor (2026-10-06, HANDOFF.md TC 추가 후보 1번)
- 백업: `automation/backups/20261006_103851/spec_v2.xlsx.bak`
- 배경: `(encode) ====== Encoding Handle Status for 60 seconds` 로그가 4개 DUT 전부(vmc8pro/crx3212/trx3208/xvm508)에서 60초 주기로 안정적으로 찍히며, 채널별 vloss(N/Y)·frame(fps,I)·해상도·bitrate를 담고 있음을 실측 확인. 프레임 드롭 감시용 신규 TC로 반영.
- 설계 (PRODUCT=`*`, TAG=`encode,regression`):
  - STEP0 ACTION=ACC_CYCLE
  - STEP1 CHECK `(LogWriter) BOOT` TIMEOUT=300(ACTION) — boot_timeout_s 300s와 동일 기준(TC001 전례)
  - STEP2 CHECK `(encode) ====== Encoding Handle Status for 60 seconds` TIMEOUT=180(PREV_STEP) — BOOT→최초 encode 로그 간격 실측 56~132s(4개 DUT), 여유있게 180s
  - STEP3 CHECK 동일 VALUE 재확인, TIMEOUT=90(PREV_STEP) — 아래 "설계 함정" 참고
  - STEP4 ABSENT SCOPE=TC, REGEX `\(encode\)\s+\d+:\s+Y\s+[1-9]` — Rec Ch/Net Ch 공통 포맷에서 비활성 채널은 항상 `vloss=Y` + `frame=0`이라 `[1-9]`로 좁혀 오탐 방지
- **설계 함정 발견/수정**: 처음엔 STEP2(헤더 줄 매칭) 하나로 끝내고 바로 STEP3(ABSENT)로 넘어가게 짰더니, STEP2가 "===== Encoding Handle Status ====" 헤더 줄에서 매칭되는 즉시 TC가 끝나버려 그 뒤에 흐르는 실제 채널 데이터 줄(vloss 값이 들어있는 부분)이 ABSENT 스캔 구간에서 통째로 빠지는 문제가 있었음. 가짜 드롭 라인을 주입한 로그로 돌려봤더니 FAIL 안 뜨고 통과해버려서 발견. STEP3에 "같은 로그의 다음 주기 발생"을 한 번 더 CHECK로 걸어 그 사이 채널 데이터 블록 전체가 ABSENT 스캔 대상에 들어가도록 수정.
- 검증: vmc8pro 기존 로그(`artifacts/vmc8pro/uart.log`)를 `--mode offline --replay ... --no-stimulus -k TC209`로 재생 — 원본 그대로는 PASS, 활성 채널에 `vloss=Y`+프레임수>0 가짜 라인을 주입한 로그는 FAIL(ABSENT 위반으로 정확히 해당 라인 지목) 확인.
- `harness.spec.load()`로 재검증 완료 (TC209, 5스텝 정상 파싱).
- **별개로 발견한 기존 버그(이번 작업 범위 아님, 수정 안 함)**: `conftest.py`의 `dut` 픽스처 offline 분기(mode=="offline")는 `OfflineDut`을 만들 때 `--no-stimulus` 옵션을 전혀 반영하지 않음(live 분기에만 `d.no_stimulus = ...` 설정 있음). 그 결과 offline+`--no-stimulus` 조합에서도 `OfflineDut.do_action()`이 실제로 로그에서 ACC_CYCLE 앵커(`(micom) ACC Remove`)를 찾으려 시도함 — crx3212/trx3208 로그로 TC209(및 기존 TC001)를 같은 옵션으로 돌렸더니 "로그에서 자극 지점을 못 찾음"으로 반복 실패(vmc8pro/xvm508은 우연히 통과). FileLogSource가 파일을 스레드로 채우는 속도와 `do_action`의 `next_line(0)`(즉시 읽기, 못 찾으면 바로 DutTimeout) 소비 속도 간 경합으로 보임. 실제 운영(live 모드)에는 영향 없음 — `--no-stimulus`는 live 분기에선 정상 적용됨. offline 재생으로 신규 TC를 검증할 땐 이 한계를 감안해서 DUT를 바꿔가며 재현성을 확인할 것.

## Driver ID (TC102/TC109/TC152) 라이브 탐색 — 자동화 불가 확정 (2026-10-06)
- 배경: HANDOFF.md "TC 추가 후보" 2번. TC123(SHELL_CHECK로 `/tmp/system/actual_setting.ini` 직접 조작)처럼 Driver ID 변경도 셸로 흉내낼 수 있는지 조사.
- GitLab(`fw/dashcams/ithaca`, 브랜치 `ithaca_2.0.x`) 소스 추적 결과:
  - Driver ID 변경은 RFID 카드 태그(`app/src/dr_app/MicomThread.cpp`, 시리얼 카드리더 `mExtDevice1`/`mNFC`) 또는 QR 코드 스캔(`app/src/dr_app/QRcodeThread.cpp`, 카메라로 `@@@@@ Decoded QR code` 디코딩)이라는 순수 하드웨어 입력 이벤트로만 트리거됨.
  - 둘 다 `CGlobalSystem::SetDriverID()`(`app/lib/globalSystem.cpp:852`)를 호출하는데, 이 함수는 런타임 메모리 변수(`mDriverID`)만 바꾸고 디스크 설정파일을 읽거나 쓰지 않음 — TC123과 달리 셸로 미리 써둘 대상 파일 자체가 없음.
  - `app/src/net_dms4/uploader.cpp`의 `OnDriveData()`가 "새 ID 프레임"(`DRV_EXTRA_NEW_ID`)을 감지하면 `CallNewDrvTrigger()`로 즉시 업로드 트리거 (로그: `interval trigger HH:MM:SS`, 단 주기 트리거와 공유돼서 그것만으론 구분 안 됨).
  - 실제 crx3212 부팅 로그에 `/mnt/mmc1/device_info/driverid.ini` 파일 경로가 등장하지만, 이건 EMMC 디스크 마운트 때마다 지웠다 재확인하는 부팅 1회성 초기화 로직(`EmmcProbe`/`DiskMgr.cpp::SetDrvInfo`)일 뿐 RFID/QR 실시간 트리거 경로와 무관.
- 라이브 콘솔(COM41/COM42) 탐색 시도: `crx-continuous`(`continuous_log.py`, 와치독 있음 — 죽이면 자동 재시작됨)가 포트를 점유 중이라 처음엔 열리지 않았고, 사용자 승인 하에 crx3212/trx3208용 프로세스를 잠시 중지→콘솔 확인→재시작함. 콘솔 자체는 열렸지만 로그인 프롬프트/echo 응답이 전혀 없어(디버그 로그 전용 출력으로 보임) 셸 명령까지는 못 들어감 — 더 깊이 들어가려면 Ctrl+C 등으로 포그라운드 프로세스를 건드려야 하는데 운영 중인 dvr_app을 방해할 위험이 있어 중단. 작업 종료 후 4개 DUT 모두 원상 복구 확인(continuous 로그 정상 갱신 중).
- 결론: TC77/95/131/186과 같은 범주(물리적 조작 전제조건, 구조적 자동화 불가)로 최종 분류. `spec_v2.xlsx`의 TC102/TC109/TC152 BLOCKED_BY를 위 근거로 구체화함. `project_crx_harness_not_automatable.md`에도 반영.
- `harness.spec.load()`로 재검증 완료.

## harness/dut.py 버그 수정: ensure_login() 의 echo 마커가 로그인 계정명 자리로 흘러들어가는 문제 (2026-10-06)
- 배경: 11:59 전체 4대 실행 결과 전수 분석 중 crx3212 TC039 가 "로그인 거부됨(2회)" InfraError 로 실패. 같은 실행에서 "Login incorrect"가 총 4번 발생(그중 3번은 재시도로 통과, TC039만 재시도도 실패).
- 원인: `ensure_login()`의 `elif state in ("IDLE", "PWD") and prompt_rx.search(t):` 분기(dut.py 339번 줄 부근)가 `#`/`$` 로 끝나는 로그 조각을 보자마자 **즉시** `echo PRB_xxx_OK` 마커를 전송했음. 로그가 쏟아지는 환경에서 이 조각 바로 뒤에 `login:` 프롬프트가 이어지면, 이미 전송한 마커 문자열이 그 프롬프트의 계정명 자리로 들어가 버림 — 실제 uart.log 증거: `CRX3212 login: echo PRB_876335_OK` (echo 문자열이 login: 프롬프트에 바로 붙어서 찍힘). 그 뒤 Password 입력도 꼬여서 "Login incorrect"로 이어짐.
- 수정: SHELL 후보로 전환은 하되(`state="SHELL"`), echo 마커 전송은 그 자리에서 하지 않고 tag를 None으로 둔 채 넘김 — 실제 전송은 기존에 있던 "3초간 추가 로그 없이 조용함" 분기(`line is None` 시 `state=="SHELL"`이면 echo 전송)로 미룸. 그 사이 `login:`/`Password:` 가 오면 해당 분기가 먼저 걸려 상태가 LOGIN/PWD 로 바로 정정되므로 마커가 끼어들 틈이 없어짐.
- `python -c "import harness.dut"` 로 문법 검증 완료. 실제 장비 재현 검증은 다음 실행 결과로 확인 예정 — crx3212 로그인 거부 재발 여부를 지켜볼 것.
- **참고(VMC8PRO TC001 재분석, 중요 정정)**: 같은 날 TC001(vmc8pro) "ACC Remove도 SHUTDOWN도 안 나왔다"는 InfraError를 처음엔 "신호가 아예 없었다"고 결론 내렸으나, 전체 uart.log를 epoch 대조로 다시 보니 **ACC Remove 자체는 실제로 로그에 있었음** — 다만 자극 시작(11:59:14.73)으로부터 acc_remove_timeout_s(15s)를 훨씬 넘겨 약 328초 뒤에야 나타났고, SHUTDOWN도 마찬가지로 지각함. 같은 재부팅 사이클에서 `(LogWriter) BOOT` 로그도 함께 누락됨(FW_VERSION은 찍혔는데 BOOT만 없음). 왜 이렇게 지각했는지(PDU 통신 지연 vs 로그 캡처 파이프라인 지연 vs FW 자체 지연)는 `harness/pdu.py`의 재시도/verify 타임아웃 값(수 초 단위)만 봐서는 설명이 안 돼 아직 단일 원인을 특정 못함 — 기존 "간헐적 캡처 유실" 카테고리의 더 극단적인 사례로 잠정 분류, 추가 재현 시 재조사 필요. **"신호가 전혀 없다"는 이전 결론은 철회.**
- **추가 발견(같은 분석 중): `continuous_log.py`(crx-continuous 상시 프로그램)가 4대 DUT 전부에서 COM 포트를 상시 점유 중인데, 이번 11:59 실행이 `--source direct`(기본값)로 돌아서 포트 공유 경합이 있었음을 확인.** 사용자 확인 결과 동일 가설 제기 → TC001 지연/BOOT 누락, TC006/TC002 간헐적 미검출, ensure_login 버그가 유독 자주 터진 것 전부 이 경합으로 설명 가능. **대응: 2026-10-06 18:00경부터 `--source daemon` 필수 + 2시간 주기 자동 회귀 루프 가동 시작**(`automation/PLAYBOOK.md`, `state.json` 참고. 종료 2026-10-07 09:00).

## Cycle 0 (2026-10-06, 사용자가 16:05 직접 `--source daemon` 실행, 2시간 주기 루프의 시작점)
- 결과: crx3212 31P/2F(TC006,TC039) · trx3208 34P/1F(TC015) · vmc8pro 31P/2F(TC004,TC020) · xvm508 34P/0F.
- **신규 버그 확정 및 수정: `config/duts.yaml`의 `boot_timeout_s` 가 여전히 180 이었음.** 2026-10-01에 `harness/dut.py`의 `DutConfig.boot_timeout_s` 클래스 기본값은 180→300으로 올렸지만, `duts.yaml`의 `defaults.boot_timeout_s: 180` 명시값이 그 기본값을 덮어써서 **실제로는 계속 180이 적용되고 있었음** — "180→300 상향" 자체가 절반만 적용된 미완성 수정이었음. 증거: TC020(vmc8pro)이 SOFT_CYCLE 액션 내부 BOOT 대기에서 `패턴 미검출 (180s): \(LogWriter\) BOOT`로 실패 — TC020 자체의 spec TIMEOUT(400s, step1 `(RecExt) close record file(`)과 무관한 메시지라 액션 내부 타임아웃임이 명확. `config/duts.yaml`의 `boot_timeout_s`를 300으로 수정 완료. 백업: `automation/backups/cycle0_20261006_181718/`. `python -c "import yaml; ..."`로 값 반영 확인.
  - 참고: `harness/duts.yaml`이라는 안 쓰이는 중복 파일이 있음(아무 코드에서도 참조 안 됨, grep 확인) — 혼동 소지 있으나 이번 사이클 범위 밖이라 안 건드림.
- **나머지(TC006/TC039/TC004/TC015)는 기존 "산발적 랜덤 미검출" 카테고리로 판단, 수정 안 함(기록만)**:
  - TC006(CRX3212, NET_IGNITION_INIT 300s 미검출): 오늘 11:59 실행 때도 TRX3208에서 같은 TC가 같은 사유로 실패했음(그때는 direct 모드+포트경합 의심). 이번엔 daemon 모드인데도 재현됨, DUT만 바뀜 — "포트경합이 유일한 원인은 아니다"는 신호지만, 아직 2회뿐이고 매번 다른 DUT/다른 TC가 랜덤하게 걸리는 기존 패턴과 일치해 추가 조치 없이 관찰만 지속.
  - TC039(CRX3212, 600s 내 미검출): **로그인 거부(ensure_login 버그) 재발 없음 — 수정 유효성 확인됨(좋은 신호).** 이번엔 순수 CHECK 타임아웃(ExtProbe 디스크 프로브 단계 중 하나)으로 실패, 산발적 미검출로 분류.
  - TC004(VMC8PRO, RECORD ON(SD) 30s 미검출): step0(SOFT_CYCLE)·step1(240s)은 통과했으니 boot_timeout_s와 무관, 디스크 마운트 지연 등으로 추정. 기존 baseline 랜덤 미검출 범주.
  - TC015(TRX3208, 420s 내 미검출): 메모리상 TC015는 vmc/xvm 쪽 baseline 기록이었는데 trx3208에서도 같은 성격으로 재현 — 기존 baseline 범주에 trx3208도 추가해서 관찰.
- `harness.spec.load()` 영향 없음(spec 변경 없음, duts.yaml만 수정). 다음 사이클(cycle 1)은 이 분석 직후 바로 시작.

## Cycle 1 (2026-10-06 18:18 ~ 19:38, daemon 모드)
- 결과: crx3212 33P/0F · trx3208 35P/0F · vmc8pro 31P/2F(TC002,TC013) · xvm508 34P/0F. **4대 중 3대 완전 통과** — 오늘 11:59 direct 모드 실행(4대 전부 1~2건씩 실패) 대비 뚜렷하게 개선됨. daemon 모드 전환 효과로 잠정 판단.
- TC002(vmc8pro, FW_VERSION 270s 미검출): 오늘 두 번째 관찰(오전엔 xvm508). 기존 "산발적 랜덤 미검출" 카테고리, 수정 안 함.
- TC013(vmc8pro, RECORD ON(SD) 270s 미검출, RECORD OFF(SD)는 PASS): **주목할 패턴** — cycle 0의 TC004(vmc8pro, RECORD ON(SD) 30s 미검출)와 같은 성격이 같은 DUT에서 2회 연속 재현됨. 같은 run 안에서 TC014(EMMC)/TC015(HDD)의 RECORD ON/OFF는 전부 정상 PASS — SD만 유독 불안정. 아직 2회뿐이라 수정은 보류하지만, **"vmc8pro RECORD ON(SD) 로그가 다른 디스크 대비 간헐적으로 더 잘 빠진다"는 가설을 메모리에 남겨 다음 사이클에서 재현되는지 계속 볼 것.**
- 확실한 수정 없음(둘 다 애매한 범주). 다음 사이클(cycle 2)은 이 분석 직후 바로 시작.

## Cycle 2 (2026-10-06 20:22 ~ 21:47, daemon 모드)
- 결과: crx3212 32P/1F(TC039) · trx3208 34P/1F(TC004) · vmc8pro 32P/1F(TC036) · xvm508 34P/0F. 매번 다른 TC가 1건씩 걸리는 기존 baseline 산발적 패턴 유지(cycle1보다 소폭 증가했으나 direct 모드 때의 4대 전체 패턴과는 거리가 있음).
- **TC039(crx3212) 3번째 관찰(오전 로그인거부 1회 + cycle0 600s미검출 + 이번 600s미검출), cycle1에서는 PASS.** 완전 100% 재현은 아니지만 crx3212에서 유독 자주 걸리는 경향 — 계속 추적.
- **vmc8pro RECORD ON(SD) 가설 기각**: 이번 cycle에서 TC004 RECORD ON(SD) 정상 PASS 확인 — cycle0/1의 2회 연속은 우연이었던 것으로 판단, 더 이상 "SD 특이적" 가설 유지 안 함.
- TC004(trx3208, 240s 미검출), TC036(vmc8pro, "vo_rear_rca is 2" 60s 미검출)은 둘 다 신규 조합이지만 1회성이라 기존 산발적 카테고리로 분류, 수정 안 함(기록만).
- 확실한 수정 없음. 다음 사이클(cycle 3) 바로 시작.

## Cycle 3 (2026-10-06 22:25 ~ 23:47, daemon 모드)
- 결과: crx3212 33P/0F · trx3208 35P/0F · vmc8pro 32P/1F(TC006) · xvm508 34P/0F. **3대 완전 통과, 1건만 산발적.**
- TC006(vmc8pro, NET_IGNITION_INIT 300s 미검출): 기존 패턴 재확인(지금까지 crx3212 2회, trx3208 1회, 이번에 vmc8pro) — "모든 제품에서 랜덤하게 걸리는 산발적 미검출"로 재확인, 수정 안 함.
- TC039(crx3212)는 이번엔 PASS — 3연속 중 2회 실패했던 경향이 이번엔 안 보임, 100% 재현 아님을 재확인.
- 확실한 수정 없음. 다음 사이클(cycle 4) 바로 시작.

## Cycle 4 (2026-10-07 00:27 ~ 01:51, daemon 모드)
- 결과: crx3212 32P/0F/1장비오류(TC017) · trx3208 34P/1F(TC034) · vmc8pro 32P/1F(TC006) · xvm508 34P/0F. 전부 기존에 이미 알려진 범주 — 새 이슈 없음.
- TC017(crx3212, ACC Remove/SHUTDOWN 둘 다 안 나옴, CH8): TC001(2026-10-06 11:59, direct 모드)과 동일 패턴이 **daemon 모드에서도 재현** — 포트경합이 유일한 원인이 아니라 랙 자체의 근본적 불안정성(CP2108 공유 하드웨어 노이즈 추정, 기존 결론)이 맞다는 걸 재확인. 수정 안 함.
- TC034(trx3208, 900s 내 미검출): 메모리에 이미 기록된 "조건부 미발생(디스크 사용량 임계치 등 조건부 로직 추정, 타임아웃 늘려도 해결 안 될 수 있음)" 기존 이슈 그대로 재현. 재조사 안 함.
- TC006(vmc8pro): 5번째 관찰(crx3212×2, trx3208×1, vmc8pro×2) — baseline 패턴으로 완전히 자리잡음.
- 확실한 수정 없음. 다음 사이클(cycle 5) 바로 시작.

## Cycle 5 (2026-10-07 02:29 ~ 03:48, daemon 모드)
- 결과: crx3212 32P/1F(TC039) · trx3208 35P/0F · vmc8pro 32P/1F(TC017) · xvm508 34P/0F.
- TC039(crx3212): 4번째 관찰, 기존 패턴 그대로.
- TC017(vmc8pro, "패턴 미검출 (300s): `\(LogWriter\) BOOT`"): TC017 자신의 spec CHECK(`(RecExt) @@@ Create File:`, 120s)가 아니라 **ACC_CYCLE 액션 내부**(acc_cycle 의 재부팅 완료 대기, `boot_timeout_s`)에서 실패 — cycle4의 crx3212 TC017(ACC Remove/SHUTDOWN 안 나옴)과 마찬가지로 "우연히 ACC_CYCLE 차례였던 TC가 TC017"이었을 뿐, TC017 고유의 문제가 아니라 ACC_CYCLE 공통 액션의 산발적 불안정성. **boot_timeout_s=300 으로 올린 지 5사이클 만에 300초도 못 맞춘 사례가 나왔다는 점은 신경 쓰이지만, 1회성이라 타임아웃을 더 올리지 않고 관찰만 지속(이미 "타임아웃 늘려도 해결 안 되는 경우가 많다"는 선례 있음).**
- 확실한 수정 없음. 다음 사이클(cycle 6) 바로 시작.

## Cycle 6 (2026-10-07 04:32 ~ 05:54, daemon 모드)
- 결과: crx3212 32P/1F(TC006,6번째) · trx3208 33P/2F(TC034,TC041) · vmc8pro 32P/1F(TC010) · xvm508 34P/0F.
- TC006(crx3212): 6번째 관찰, baseline 그대로.
- TC034(trx3208): 기존 "조건부 미발생" 이슈 재현.
- TC041(trx3208, 60s 내 미검출): 메모리에 이미 "TC014/TC041 잔여 랜덤 미검출 baseline"으로 기록된 기존 패턴 그대로.
- TC010(vmc8pro, "패턴 미검출 (300s): BOOT"): cycle5의 TC017(vmc8pro)과 **같은 성격(ACC_CYCLE/SOFT_CYCLE 내부 boot_timeout_s 300s 미달)이 같은 DUT(vmc8pro)에서 2연속 사이클째 재현.** "vmc8pro가 다른 3대보다 재부팅이 유독 느리거나 BOOT 로그가 더 자주 늦는다"는 가설이 생기지만, 정확히 몇 초 뒤에 나왔는지 실측하지 않아 "확실한 수정" 근거는 아직 부족 — 타임아웃을 또 올리지 않고 **vmc8pro 전용으로 패턴이 계속되는지만 추적**(실측 근거 쌓이면 boot_timeout_s를 DUT별로 분리하거나 더 올리는 걸 재검토).
- 확실한 수정 없음. 다음 사이클(cycle 7) 바로 시작.

## Cycle 7 (2026-10-07 06:34 ~ 07:53, daemon 모드) — 이번 루프의 마지막 사이클
- 결과: crx3212 31P/2F(TC039,TC040) · trx3208 33P/1F/1장비오류(TC007,TC008) · vmc8pro 31P/2F(TC013,TC113) · xvm508 34P/0F.
- TC039(crx3212, 5번째), TC008(trx3208, ACC Remove/SHUTDOWN 둘 다 안 나옴 — TC001/TC017 과 동일 기존 패턴), TC013(vmc8pro, RECORD ON/OFF(SD) 계열 재현): 전부 기존 알려진 범주.
- TC040(crx3212, "패턴 미검출 (90s): SHUTDOWN")·TC007(trx3208, "패턴 미검출 (30.0s): Loader Start"): 액션 내부(shutdown_pattern/loader_pattern) 타임아웃 — ACC Remove/SHUTDOWN/BOOT 미검출과 같은 "액션 내부 신호 산발적 지연·누락" 큰 카테고리에 속함, 새로운 유형 아님.
- **TC113(vmc8pro, "패턴 미검출 (300s): BOOT") — vmc8pro의 ACC_CYCLE/SOFT_CYCLE 내부 `boot_timeout_s`(300s) 미달이 cycle5(TC017)·cycle6(TC010)·cycle7(TC113) 로 3사이클 연속 재현.** 4대 중 vmc8pro에서만 이 패턴이 나타남(다른 DUT는 0회). 이제 "완전 우연"으로 보기 어려운 수준 — **다음에 이어서 볼 사람은 이 가설부터 검증할 것: vmc8pro 실제 로그에서 재부팅 시작~BOOT 사이 걸린 시간을 실측해서 (a) 진짜로 300s를 넘기는 건지 (b) BOOT 로그 자체가 간헐적으로 유실되는 건지 구분 — (a)면 vmc8pro 전용으로 boot_timeout_s를 추가로 올리는 걸 검토, (b)면 기존 "간헐적 캡처 유실" 카테고리로 편입.** 아직 코드/스펙 수정은 안 함(실측 전).
- 확실한 수정 없음.

## 2시간 주기 자동 회귀 루프 종료 (2026-10-07 08:37, 예정된 종료 시각 09:00 근접)
- 총 8회 실행(cycle 0~7, 2026-10-06 16:05 ~ 2026-10-07 07:53), cycle 1부터 daemon 모드로 전환.
- **확정 수정 2건**: ① `harness/dut.py`의 `ensure_login()` 이 echo 마커를 로그인 프롬프트 계정명 자리로 흘려보내던 버그(cycle 이후 재발 0회, 수정 유효성 확인됨). ② `config/duts.yaml`의 `boot_timeout_s` 가 180으로 남아있던 설정 불일치(300으로 수정).
- **daemon 모드 전환 효과 확인**: direct 모드였던 10/6 11:59 실행(4대 전부 1~2건씩 실패)과 비교해, daemon 모드 전환 후(cycle1~7)에는 4대 중 2~3대가 완전 통과하는 사이클이 다수 — `continuous_log.py` 와의 포트 경합이 당일 오전 실패의 주요 원인이었다는 가설이 데이터로 뒷받침됨.
- **남은 산발적 이슈(수정 안 함, 계속 관찰 대상)**: TC006/TC039(여러 DUT에서 반복, 완전 랜덤), TC034(기존 조건부 미발생), TC041(기존 baseline), ACC Remove/SHUTDOWN/BOOT/Loader Start 액션 내부 신호의 산발적 지연·누락(여러 TC에 귀속되어 나타남, CP2108 공유 하드웨어 노이즈로 추정하는 기존 결론 유지).
- **신규로 패턴화된 관찰(다음 세션 조사 후보)**: vmc8pro 의 ACC_CYCLE/SOFT_CYCLE 내부 BOOT 300s 미달이 3사이클 연속(cycle5/6/7) — 위 Cycle 7 항목 참고.
- state.json `phase`를 `idle`로, `end_date`(09:00) 도래로 새 사이클 시작 안 함. 상세 로그/summary 는 각 `C:\Users\HW-RACK\Desktop\log\<DUT>\2026-10-06-16시05분24초` ~ `2026-10-07-06시34분48초` 사이 폴더들.

## 루프 종료 후 추가 조사: vmc8pro BOOT 지연 원인 확정 + 교차 확인 로직 추가 (2026-10-07)
- cycle5(TC017)/cycle6(TC010)/cycle7(TC113) 세 run 각각의 uart.log를 `Loader Start` 전후로 직접 대조한 결과, **3건 모두 완전히 동일한 패턴**: ACC Remove→SHUTDOWN→Loader Start까지는 평소와 같은 속도로 진행되고, 그 뒤 다른 모든 정상 사이클은 44~47초 만에 `(LogWriter) BOOT`가 찍히는데 이 3건만 그 로그가 통째로 없이 바로 다음 사이클의 ACC Remove(약 330~360초 뒤, 즉 boot_timeout_s 300s를 다 쓴 뒤의 폴링 종료 시점과 일치)로 넘어감. **결론: 재부팅 자체는 매번 정상 속도로 완료됐고, `(LogWriter) BOOT` 로그 한 줄만 간헐적으로 캡처가 안 된 것** — "장비가 느려서"가 아니므로 `boot_timeout_s`를 더 올려도 의미 없음 확정(가설 (a) 기각, (b) 확정).
- vmc8pro에서만 3연속 재현된 이유는 특정 못함(3회로는 "vmc8pro 전용 결함"이라 단정하기엔 이름 — 계속 관찰 필요).
- **사용자 승인 하에 수정**: `harness/dut.py`의 `acc_cycle()` — `wait_log(boot_done_pattern, boot_timeout_s)`가 `DutTimeout`을 내면, 기존 ACC Remove/SHUTDOWN 교차 확인과 같은 철학으로 `ensure_login()`을 시도해 실제 셸 응답이 오는지 확인. 응답이 오면(= 장비가 실제로는 정상 재부팅됨) BOOT 로그 누락으로 보고 경고만 남기고 통과, 응답이 없으면(`ensure_login()`이 자체적으로 `InfraError`를 던짐) 진짜 부팅 실패로 그대로 전파.
- **`soft_cycle()`에는 같은 교차 확인을 추가하지 않음** — 지금까지 재현된 3건이 전부 `ACC_CYCLE` 액션 경로였고(TC017/TC010/TC113 전부 STEP0 ACTION=ACC_CYCLE), `soft_cycle()` 쪽 BOOT 미검출로 인한 실패 사례는 아직 관찰된 적 없음. 같은 구조적 리스크(BOOT 로그 캡처 누락)가 이론상 거기도 있을 수 있으니, `soft_cycle()`에서 같은 증상이 실제로 재현되면 그때 동일 패턴 추가할 것.
- `python -c "import harness.dut"` 문법 검증 완료. **이 수정은 live 모드에서만 타는 코드 경로라 오프라인 재생으로 검증 불가** — 다음 실제 실행(루프 재개든 수동 실행이든)에서 vmc8pro의 BOOT 미검출이 "경고 + 통과"로 바뀌는지, 그리고 실제로 로그인도 안 되는(= 진짜 고장) 경우를 놓치지 않고 InfraError로 올라가는지 확인 필요.

## 2026-10-13 FOTA 이후 XVM508 장비오류 급증 분석 + wait_log() 구조적 버그 수정
- 배경: 10/13 09:00경 4대 전부 FW 업데이트(FOTA로 추정: crx3212/trx3208/vmc8pro `f55c0c_261006`→`bfce8b_261008`, xvm508 `3a60fa_260929_1`→`5caa36_261012`). 그 직후 전체 실행 2회(09:09, 11:53) 모두 **xvm508에서만 SOFT_CYCLE 관련 장비오류가 매회 4건씩** 발생(TC003/013/019 등, 전부 "SHUTDOWN 90s 미검출"→InfraError).
- **근본 원인 확정(FW 버그 아님, 하니스 구조적 버그): `harness/dut.py`의 `wait_log()`가 `action_buf`를 먼저 재확인하지 않음.** `acc_cycle()`/`soft_cycle()`은 SHUTDOWN→terminated→BOOT 순서로 `wait_log()`를 연달아 호출하는데, 예를 들어 'terminated' 30초 대기 창 안에 BOOT 로그가 먼저 지나가버리면 그 줄은 `action_buf`에 쌓이기만 하고, 바로 다음 BOOT 대기(300초)는 `next_line()`으로 "새로 들어오는 줄"만 보기 때문에 이미 지나간 BOOT을 영영 못 찾고 타임아웃을 전부 소진함.
  - **실측 증거 (xvm508, 2026-10-13-09시08분57초 run, TC013)**: `09:41:31~09:42:01` terminated 30초 대기 창 안인 `09:42:00.095`에 정확히 `(LogWriter) BOOT`가 찍혔는데, 그 직후 시작된 BOOT 300초 대기가 이미 지나간 그 줄을 못 찾고 `09:47:01`까지 전부 소진 → InfraError.
  - `verdict.py`의 TC 판정 로직(`_await_one`)은 이미 `action_buf`를 먼저 훑는 방식이라 이 문제가 없었음 — `acc_cycle`/`soft_cycle` 내부 전용인 `wait_log()`만 그 보호가 빠져 있었던 것.
- **수정 1 (근본 수정)**: `wait_log()` 시작 시 `action_buf`에 이미 쌓인 줄들을 먼저 스캔하도록 추가. 유닛 테스트(가짜 소스로 action_buf 선매칭/스트림 매칭/완전 미검출 3가지 시나리오) 통과 확인.
- **수정 2**: `soft_cycle()`에도 `acc_cycle()`과 동일한 BOOT 미검출 시 `ensure_login()` 교차 확인 추가 — vmc8pro TC004(SOFT_CYCLE 경로, 9/13 09:09 실행에서 "BOOT 300s 미검출"로 실패)가 `acc_cycle()`에만 넣었던 지난 수정의 사각지대였음.
- 두 수정 다 `python -c "import harness.dut"` 문법 검증 완료. 수정 1은 유닛 테스트로 로직 검증 완료, 수정 2는 수정 1과 마찬가지로 live 모드 전용이라 실제 장비 재현 검증은 다음 실행에서 확인 필요.
- **TC003(같은 run)은 이 가설만으로 설명 안 됨**: BOOT(09:15:33.115)이 이론상 "BOOT 대기 창" 시작 이후에 있어서 타이밍상 action_buf 문제가 아닐 수 있음 — daemon 로그 소스의 실시간성 지연 등 다른 요인이 섞여 있을 가능성, 완전히 해소됐는지는 다음 실행으로 재확인.
