# CRX FW 자동 테스트 하네스

시트가 진실의 소스입니다. 코드를 생성하지 않고 런타임에 엑셀을 읽습니다.
시트를 고치면 다음 실행에 바로 반영됩니다.

---

## 1. 구조

```
spec_v2.xlsx          테스트 명세 (여기만 고치면 됩니다)
config/
  dut_crx.yaml        DUT 프로파일 (포트, 타임아웃)
  forbidden.yaml      전 테스트 공통 금지 패턴
  known_issues.yaml   금지 패턴에서 제외할 알려진 이슈
harness/
  spec.py             시트 로더
  logsource.py        시리얼 단일 채널 분배 / 오프라인 재생
  dut.py              로그인, 마커 명령, 자극 3종
  verdict.py          CHECK / ABSENT 판정
conftest.py           pytest 설정, 시트 파라미터화
test_spec.py          테스트 본체 (함수 1개)
```

테스트 함수는 하나뿐입니다. 케이스 38개는 전부 시트에서 나옵니다.

---

## 2. 설치

```bash
pip install pytest pyserial openpyxl pyyaml
```

비밀번호는 코드나 설정 파일에 넣지 마세요.

```bash
# Windows
set DUT_PASSWORD=<비밀번호>
# Linux
export DUT_PASSWORD=<비밀번호>
```

레포에 올릴 때 `.gitignore` 에 `artifacts/` 를 넣으세요.

---

## 3. 1단계 — 오프라인으로 판정식부터 잡기

**실제 DUT 없이 캡처된 로그로 먼저 돌립니다.** 실시간에서 테스트가 깨지면
FW 결함인지, 자극이 잘못된 건지, 판정식이 틀린 건지 3중으로 헷갈립니다.

```bash
pytest test_spec.py --mode offline --replay "2026-09-01-13시17분32초.log" --product CRX -v
```

`FAIL` 이 나오면 세 가지 중 하나입니다. 순서대로 확인하세요.

1. **시트 VALUE 가 실제 로그와 다름** → 로그에서 `grep` 해보고 시트 수정
2. **TIMEOUT 이 짧음** → 실측해서 시트 수정
3. **진짜 결함** → 티켓

에이징 로그 하나로 판정식 대부분이 잡힙니다. 여기서 깨끗해진 뒤 넘어가세요.

---

## 4. 2단계 — 실시간 실행

먼저 `harness/dut.py` 의 `RelayController.set()` 을 실제 릴레이 API 로 채웁니다.
채널 on/off 만 구현하면 됩니다.

```bash
pytest test_spec.py --mode live --product CRX -v
```

`config/dut_crx.yaml` 에서 `serial_port` 를 실제 COM 포트로 바꾸세요.

### 자극 3종

| ACTION | 동작 | 확인된 것 |
|---|---|---|
| `POWER_CYCLE` | 릴레이 off → 8s → on | 진짜 콜드부트 |
| `SOFT_CYCLE` | `killall -TERM dvr_app` → 스크립트가 poweroff → MICOM 재기동 | SHUTDOWN·UNMOUNT·RECORD OFF 전부 나옴 |
| `HARD_REBOOT` | shell `reboot` | RECORD OFF 까지만. SHUTDOWN 미도달 |

`SOFT_CYCLE` 이 핵심입니다. `/etc/init.d/driverec` 이 `dvr_app` 종료를 감지해
`poweroff` 를 걸고, MICOM 이 ACC 유지 상태를 보고 다시 켭니다.
릴레이 없이도 완전한 종료-재기동 사이클이 돕니다.

실측 (2026-09-01 에이징 로그, 4회 부팅):

```
Record[INT] Off  →  SHUTDOWN          45.6s
SHUTDOWN         →  poweroff           6.3s
poweroff         →  U-Boot             7.6s
Loader Start     →  (LogWriter) BOOT  44.2 ~ 45.0s
Record[INT] Off  →  ACC ON 재기동     101.4 ~ 106.8s
```

### 처음 돌릴 때 순서

1. `off_settle_s` 실측 — 3/5/8/15초로 바꿔가며 부트로더 리셋 원인이
   콜드부트로 일관되게 찍히는 최소값을 찾습니다
2. 태그 하나로 좁혀서 시작 — `-k "TC001 or TC002"`
3. 통과하면 `boot` 태그 전체, 그 다음 전체

---

## 5. 3단계 — CI 연동

```bash
pytest test_spec.py --mode live --product CRX \
       --junitxml=result.xml -v
```

`result.xml` 을 GitLab CI 의 `artifacts:reports:junit` 에 물리면
MR 화면에 결과가 붙습니다.

`InfraError` 는 별도 마커(`infra`)로 분리됩니다. 릴레이 고장이 "FW 테스트 실패"로
집계되면 리포트 숫자가 거짓말을 하고, 그러면 개발팀이 리포트를 안 봅니다.

실행 끝에 커버리지 요약이 찍힙니다.

```
시트 전체 TC     : 213
CRX 대상 TC      : 210
실행 가능        : 38
blocked          : 170
    - 검증 조건 미정의(Jira 이슈 원문): 167
    - ACC 릴레이 미구성: 1
    - G센서 충격 자극 필요: 1
    - 리모컨/키 입력 필요: 1
```

**`blocked` 를 숨기지 않는 게 중요합니다.** "장비 하나 없어서 N개가 막혀 있다"가
숫자로 남아야 나중에 예산 얘기할 때 근거가 됩니다.

---

## 6. 시트 고치는 법

| 컬럼 | 값 |
|---|---|
| `TYPE` | `CHECK` 나와야 함 / `ABSENT` 나오면 안 됨 / `ACTION` 자극 |
| `VALUE` | CHECK·ABSENT: 찾을 문자열 / ACTION: 자극 이름 |
| `FAIL` | 이 문구가 나오면 TIMEOUT 안 기다리고 즉시 실패 |
| `TIMEOUT` | 초. 비면 `default_timeout_s` |
| `TIMEOUT_FROM` | `ACTION` 자극 완료 시각 / `PREV_STEP` 직전 스텝 매칭 시각 / `ANY` 로그 전체 |
| `ORDERED` | `Y` 시트 순서대로 / `N` 순서 무관 |
| `SCOPE` | ABSENT 전용. `TC` 실행 구간 전체 |
| `BLOCKED_BY` | 값이 있으면 실행 안 하고 blocked 로 집계 |
| `PRODUCT` | `*` 공통 / `CRX` `TRX` `XVM` 해당 제품만 |

TC 를 추가하려면 행만 넣으면 됩니다. 코드는 안 건드립니다.

`FAIL` 컬럼을 채울수록 실행 시간이 줄어듭니다. 지금 6행뿐인데,
TIMEOUT 이 600초인 행이 4개, 3600초인 행이 2개 있습니다.
이 둘은 야간 잡으로 빼세요.

---

## 7. 금지 패턴 다루는 법

`config/forbidden.yaml` 은 전 테스트에 자동 적용됩니다. 개별 TC 가
아무것도 안 해도 커널 패닉·OOM·스택 트레이스를 잡습니다.

**새 패턴을 넣기 전에 반드시 기존 로그로 먼저 돌려보세요.** 정상 상태에서
이미 나오는 패턴을 넣으면 100% 실패합니다.

실제로 이 제품은 매 부팅마다 커널 스택 트레이스가 찍힙니다. 그래서
`known_issues.yaml` 에 예외로 등록해 뒀습니다. 예외는 **줄 단위가 아니라
앞뒤 40줄 문맥**으로 판단합니다. `Call trace:` 줄 자체에는 드라이버 이름이
없어서 줄 단위로는 안 걸러집니다.

예외를 추가할 때는 `ticket` 과 `evidence` 를 반드시 채우세요.
근거 없는 예외가 쌓이면 금지 패턴 전체가 무력화됩니다.

---

## 8. 모니터 화면

```bash
python monitor.py
```

브라우저가 자동으로 열립니다. 외부 CDN 을 안 쓰므로 인터넷이 막힌 PC 에서도
그대로 동작합니다.

**모니터를 켜둔 채로 다른 터미널에서 pytest 를 돌리세요.** 모니터가 pytest 를
실행하지 않습니다. 이벤트 파일과 로그 파일을 읽기만 합니다.

```
터미널 1:  python monitor.py
터미널 2:  pytest test_spec.py --mode live --product CRX
```

이 구조라서 얻는 게 셋입니다.

- 모니터가 죽어도 테스트는 계속 돕니다. 반대도 마찬가지입니다
- pytest 쪽 수정이 conftest 훅 몇 줄로 끝납니다
- CI 에서 돌린 `events.jsonl` 도 같은 화면으로 봅니다

### 실시간 탭

지금 어느 TC 의 몇 번 스텝에서 무슨 문자열을 몇 초째 기다리는 중인지 보입니다.
지금은 타임아웃이 날 때까지 아무것도 알 수 없는데, 이게 있으면 멈춘 지점이
즉시 보입니다.

우측 UART 로그는 색으로 구분합니다. 빨강은 에러, 초록은 `(LogWriter)` 계열
주요 이벤트, 회색은 커널 메시지입니다.

### 추이 탭

- **FW 버전별 부팅 시간** — 축이 실행 시각이 아니라 FW 버전입니다.
  시각으로 잡으면 "언제 나빠졌는지"는 알아도 "어느 빌드에서"는 모릅니다
- **실행 이력** — 빌드별 통과/실패
- **자꾸 깨지는 TC** — 실패율 100% 는 진짜 결함, 40% 같은 중간값은 대개
  테스트가 불안정한 것입니다. 이 구분이 없으면 리포트 전체를 불신하게 됩니다

### 추이에 값을 추가하려면

시트의 `CAPTURE` 컬럼에 `이름:정규식` 을 넣습니다. 그 스텝이 통과할 때
값을 뽑아 실행 레코드에 붙입니다.

```
TC002 step1   fw_version:FW_VERSION\s+(\S+)
```

정규식을 빼고 이름만 쓰면 매칭된 줄 전체가 저장됩니다.

### 데이터가 쌓이는 곳

```
artifacts/events.jsonl   현재 실행 이벤트 (실행마다 새로 만듦)
artifacts/uart.log       UART 원문 (live 모드에서만)
artifacts/runs.db        SQLite. 실행 이력 누적
```

`runs.db` 만 백업하면 추이가 보존됩니다.

---

## 9. 여러 대 동시 실행

### 포트 지정

Windows 의 COM 번호는 재연결마다 바뀝니다. 4대를 돌리면 순서가 뒤바뀌어
crx1 결과가 trx1 에 찍힙니다. 하드웨어 ID 로 잡아야 합니다.

```bash
python -m harness.ports
```

나온 hwid 에서 그 포트만 유일하게 구분되는 부분을 `config/duts.yaml` 의
`port_match` 에 넣습니다.

**CP2108 은 Quad 브리지라 Interface 0~3 이 SER 을 공유합니다.**
SER 만 넣으면 4개가 동시에 걸려서 실행이 멈춥니다. `LOCATION` 까지 포함하세요.

```yaml
port_match: "SER=01A2B3C4 LOCATION=1-3:x.0"
```

`port_match` 가 비어 있으면 `serial_port` 값을 그대로 씁니다.
처음에는 COM 번호로 시작하고, 안정화되면 바꾸세요.

### 실행

```bash
python run_all.py                      # duts.yaml 전체
python run_all.py --only crx1,trx1
python run_all.py -k boot
python run_all.py --serial             # 한 대씩 순차
```

`pytest-xdist` 를 쓰지 않습니다. DUT 마다 프로세스를 따로 띄웁니다.
격리가 확실하고 한 대가 죽어도 나머지가 안 멈춥니다.
프로세스는 2초 간격으로 띄워 전원 인가가 겹치지 않게 합니다.

### 결과가 쌓이는 곳

```
artifacts/
  crx1/  events.jsonl  uart.log  result.xml  run.out
  trx1/  ...
  runs.db          공유 (dut 컬럼으로 구분)
```

`runs.db` 는 한 개입니다. DUT 별 비교가 가능해야 하기 때문입니다.

### 4대 운영의 진짜 이점

처리량이 아니라 **판별력**입니다.

| 결과 | 해석 |
|---|---|
| 4대 다 실패 | 확실한 FW 결함 |
| 1대만 실패 | 그 개체 문제 (하드웨어 편차, SD 카드 불량) |
| 매번 다른 대가 실패 | 테스트가 불안정한 것 |

1대일 때는 "실패 = 결함"으로만 볼 수 있어서 이 구분이 안 됩니다.

---

## 10. 남은 작업

**장비**
- 릴레이 채널 확장 (4대 x B+ / ACC) → TC005 해제
- `RelayController.set()` 구현

**시트**
- **VMC 제품 행 추가** — VMC8PRO 가 시트에 없어 공통(`*`) TC 만 돕니다
- Jira 167행에 검증 조건 채우기 (지금은 전부 blocked)
- `FAIL` 컬럼 확대
- TRX / XVM 행 추가 (`PRODUCT` 컬럼으로 한 파일 유지)

**확인 필요**
- `(LogWriter) MICOM_ACC_CHANGE` 문구 — 7일 에이징에 0건
- `Fail to EMMC Driver ID Load` 가 정상인지 결함인지

---

## 11. 이번 오프라인 검증 결과

2026-09-01 에이징 로그(7일, 114만 줄, 128MB) 기준.

```
35 passed, 3 failed, 3 skipped
```

실패 3건은 판정 로직 문제가 아니라 전부 실제 발견 사항입니다.

**TC030 — RTC 회귀가 아직 안 고쳐졌습니다**

```
nvt_rtc_chk_power: RTC ready timeout, plz check 32K OSC on PCB
```

4회 부팅 전부에서 재현됩니다. 시트에 `FAIL` 문구로 이미 등록돼 있던
회귀 항목입니다. 디바이스 시각이 2015/01/01 로 나오는 현상과
연관됐을 가능성이 높습니다.

**TC007 — `MICOM_ACC_CHANGE` 가 7일간 한 번도 안 나옵니다**

시트 문구가 틀렸거나, 이 이벤트가 실제로 발생하지 않는 것입니다.
개발팀 확인이 필요합니다.

**TC025 — `NETWORK FAIL` 은 자극이 필요합니다**

7일 내내 네트워크가 정상이었으니 안 나온 게 맞습니다.
네트워크 차단 수단이 생기면 해제됩니다.

**참고: 안정성은 좋습니다**

7일간 예기치 않은 재부팅 0회, 커널 패닉·OOM·소프트 락업 0건입니다.
재부팅 3회는 전부 FOTA 의도된 재부팅이었습니다.
