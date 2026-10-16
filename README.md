# CRX FW 테스트 하네스

반복되는 실장비 회귀 검증을 위해 만든 pytest 기반 테스트 하네스입니다. Excel 명세의 조건에 따라 테스트를 실행하고, UART 로그를 판정해 결과와 근거 로그를 남깁니다.

실무에서는 장비별 pytest 프로세스를 분리해 여러 DUT를 실행합니다. 이 저장소에는 장비 없이 판정과 리포트 흐름을 확인할 수 있는 가상 데이터 예제도 포함합니다.

## 장비 없이 실행하기

Python 3.10 이상을 사용합니다. 실장비 운영은 Windows 기준이며, 아래 오프라인 예제는 COM 포트·로그인·전원 제어를 사용하지 않습니다.

```bash
python -m pip install -r requirements.txt
python examples/make_demo_spec.py
python -m pytest test_spec.py --mode offline --spec examples/spec.demo.xlsx --dut-config examples/duts.demo.yaml --replay examples/normal.txt -v
```

정상 예제의 기대 결과는 **2 passed, 1 skipped, 1 xfailed**입니다. `skipped`는 자극 장비가 없는 항목, `xfailed`는 의도한 누락 로그를 사용하는 회귀 감시 항목입니다. 실제 장비의 검증 결과나 자동화 커버리지 수치가 아닙니다.

잘못된 로그가 FAIL로 검출되는지도 확인할 수 있습니다.

```bash
python -m pytest test_spec.py --mode offline --spec examples/spec.demo.xlsx --dut-config examples/duts.demo.yaml --replay examples/error.txt -k DEMO002 -v
```

이 명령은 `DEMO_ERROR`를 검출해 **1 failed** 및 종료 코드 **1**을 반환해야 합니다. 예제의 의도된 실패입니다.

`spec.demo.xlsx`는 생성 파일로 Git에서 제외합니다. 생성 코드는 [examples/make_demo_spec.py](examples/make_demo_spec.py), 입력 로그는 직접 작성한 가상 데이터입니다.

## 실행 흐름과 구성

1. `harness/spec.py`가 Excel의 `SPEC` 시트를 읽습니다.
2. `conftest.py`가 제품별 TC를 선택해 pytest에 전달합니다.
3. `harness/dut.py`가 자극을 수행하거나 오프라인 로그를 재생합니다.
4. `harness/verdict.py`가 CHECK·ABSENT·SHELL_CHECK 조건을 판정합니다.
5. 이벤트·리포트·SQLite 이력에 결과를 남깁니다.

| 경로 | 역할 |
| --- | --- |
| `test_spec.py`, `conftest.py` | TC 실행, fixture, 결과 수집 |
| `harness/spec.py` | Excel 명세 로딩과 제품 필터 |
| `harness/logsource.py` | 직접 시리얼 수신, 외부 데몬 연결, 파일 재생 |
| `harness/dut.py`, `harness/pdu.py` | 장비 명령·로그인·재부팅·전원 제어 |
| `harness/verdict.py`, `harness/check.py` | TC 및 값 판정 |
| `harness/report.py`, `harness/store.py` | 상세·요약 리포트와 실행 이력 |
| `monitor.py` | 이벤트·로그·이력 조회용 로컬 화면 |
| `config/` | 실장비 프로파일, 공통 금지 패턴과 예외 |
| `examples/` | 독립 실행용 가상 명세와 로그 |
| `docs/history/` | 과거 운영·수정 기록 |

## TC 조건

TC 조건은 Excel 시트에서 관리합니다. 전체 필수 컬럼은 `harness/spec.py`의 `HEADERS`, 최소 명세 작성법은 예제 생성 코드를 참고하세요. 기존 자극과 판정 유형의 조합은 시트에서 변경할 수 있지만, 새로운 장비 동작이나 판정 방식은 코드 수정이 필요합니다.

| 항목 | 동작 |
| --- | --- |
| `CHECK` | 문자열 또는 정규식 검출 |
| `ABSENT` | 판정 과정에서 읽은 로그 구간의 금지 문자열 검출 |
| `ACTION` | 전원·ACC 사이클, 소프트 사이클, 재부팅 등 구현된 자극 |
| `SHELL_CHECK` | 실장비 셸 명령의 출력 검사. 오프라인 미지원 |
| `TIMEOUT_FROM` | `ACTION`, `PREV_STEP`, `ANY` 기준. `ANY`는 시간 제한 없이 로그 검색 |
| `ORDERED` | `Y` 순차 검사, `N` 순서 무관 검색. `SHELL_CHECK`는 `Y`에서만 사용 |
| `BLOCKED_BY` | 사유를 기록하고 pytest skip 처리 |
| `TAG=regression_watch` | `xfail(strict=True)`: 예상 실패는 XFAIL, 예상 밖 성공은 XPASS(strict)로 실패 처리 |

**ABSENT 범위에 주의하세요.** CHECK가 모두 끝나면 해당 TC 판정도 끝납니다. 마지막 CHECK 이후의 로그까지 계속 관찰한다고 가정하면 안 됩니다. 필요한 관찰 구간을 CHECK와 자극으로 구성하고, 오류를 넣은 로그에서도 검출되는지 확인해야 합니다.

TC 수는 명세와 제품 필터에 따라 달라집니다. 실행 종료 시 출력되는 대상·실행 가능·blocked 요약을 확인하세요. 이는 명세 항목 수이며 요구사항 커버리지나 성공률과 같지 않습니다.

## 실장비 실행

운영용 `spec_v2.xlsx`와 실장비 원문 로그는 저장소에 포함하지 않습니다. 본인의 명세·장비 구성으로 준비해야 합니다. `config/duts.yaml`은 기존 랙 설정이며 그대로 실행하는 기본 예제가 아닙니다.

1. 별도 DUT 설정 파일에서 포트, 장비 이름·제품, 로그 경로를 지정합니다.
2. 전원 제어가 필요하면 `harness/pdu.py`의 허용 채널·별칭 확인과 실제 배선을 점검합니다. 외부 CoTermPowerMon 서비스는 별도 구성입니다.
3. `DUT_PASSWORD` 환경변수 또는 Git에서 제외한 `config/secrets.yaml`로 비밀번호를 전달합니다.
4. 선택한 TC 하나로 자극과 판정을 확인한 뒤 범위를 늘립니다.

PowerShell 비밀번호 설정 예시:

```powershell
$env:DUT_PASSWORD = "<장비 비밀번호>"
```

자신의 설정과 명세를 준비한 후 실행합니다.

```bash
python -m pytest test_spec.py --mode live --dut-config config/my-duts.yaml --dut my-device --spec path/to/spec.xlsx --source direct -v
python run_all.py --dut-config config/my-duts.yaml --spec path/to/spec.xlsx --only my-device
```

- `direct`: Harness가 COM 포트를 직접 엽니다. 같은 포트를 다른 프로그램에서 열지 않습니다.
- `daemon`: 외부 `crx-continuous`의 로그 파일·입력 소켓에 접속합니다. 이 외부 프로그램은 저장소에 포함하지 않습니다. 설정의 `log_dir`와 `pdu_channel`도 맞아야 합니다.
- `run_all.py`는 기본값이 **live**이며 DUT별 pytest 프로세스를 띄웁니다. 단일 pytest 실행의 기본값은 **offline**입니다. 예제처럼 모드를 명시하는 것을 권장합니다.
- Windows 파일 잠금을 사용하는 실장비 재부팅 자극은 다른 OS에서 지원하지 않습니다. 오프라인 실행은 장비 동작 검증을 대신하지 않습니다.

기존 운영 설정에는 `POWER_CYCLE`을 `SOFT_CYCLE`로 치환하는 설정이 있습니다. 이 상태의 결과를 물리 전원 차단 검증으로 해석하면 안 됩니다.

## 결과와 모니터링

기본적으로 `artifacts/<DUT>/<실행시각>/`에 `events.jsonl`, `summary.txt`, `detail_report.md`를 생성합니다. 설정에 `log_dir`가 있으면 실행 폴더는 해당 경로 아래에 생깁니다. 실시간 로그의 위치는 소스 모드에 따라 달라집니다. SQLite 실행 이력은 `artifacts/runs.db`에 누적합니다.

`run_all.py`는 DUT별 `result.xml`과 `run.out`도 저장합니다. pytest를 직접 실행할 때 JUnit이 필요하면 `--junitxml=artifacts/result.xml`을 추가합니다.

```bash
python monitor.py
```

모니터는 로컬 `http://127.0.0.1:8777`에서 결과를 조회합니다. 테스트 실행과는 별도 프로세스이며, Harness 종료 후에도 UART를 계속 수신하는 프로그램은 아닙니다.

FAIL은 제품 결함의 확정 판정이 아닙니다. 기대 로그·시간 조건·시험 전제조건·장비 상태를 함께 확인해야 합니다. 여러 DUT가 같은 조건에서 실패하면 공통 FW뿐 아니라 설정·판정 로직도 확인하고, 특정 DUT만 실패하면 개체·저장장치·연결 상태 등을 조사합니다.

공통 금지 패턴과 알려진 예외는 `config/forbidden.yaml`, `config/known_issues.yaml`에서 관리합니다. 기존 제품의 예외를 다른 제품에 그대로 적용하지 말고 근거와 적용 범위를 확인하세요.

## 구현과 검증에서 맡은 역할

생성형 AI를 코드 구현 보조로 활용했습니다. 자동화 대상과 판정 조건을 정의하고 실제 DUT에서 실행 결과를 확인하며 TC와 동작을 보완했습니다. 주요 설계는 장비별 프로세스 분리, 시트 기반 조건 관리, 단일 시리얼 수신 경로, 실패 시 근거 로그 보존입니다.

이 저장소의 공개 예제는 로그 판정과 결과 생성 흐름을 보여줍니다. 실제 장비에서의 동작·장시간 안정성은 제품·FW·명세·시험 환경별로 확인해야 합니다. 과거 시험 수치와 수정 경위는 [문서 목록](docs/README.md)에 별도로 보관합니다.

## 현재 제한

- 실제 장비·운영 명세·외부 상시 로거·PDU 서비스는 별도로 준비해야 합니다.
- 오프라인 로그가 끝났는데 기대 조건을 찾지 못했고 로그의 마지막 시각도 제한 시간을 넘지 않으면 판정 대기가 끝나지 않을 수 있습니다. 예제 누락 항목에는 제한 시간을 넘는 마지막 로그를 포함했습니다.
- 코드·운영 설정·과거 기록 사이에 시점 차이가 있을 수 있습니다. 현재 실행 조건과 원문 로그를 기준으로 결과를 확인해야 합니다.
