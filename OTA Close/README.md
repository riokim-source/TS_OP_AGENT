# OTA Close Bot

KKDAY / KLOOK / GetYourGuide(GG) / Viator(VI) / MyRealTrip(MRT) / TPC(Trip.com) 6개 OTA 의 매일 마감 자동화.

각 공급자 포털에 이미 로그인된 Chrome 에 붙어서, **다음날 신규 예약을 차단**(마감)한다.
TPC 를 뺀 5개는 Playwright 로 붙고, TPC 는 페이지 타깃에 직접 붙는다
(같은 Chrome 에 응답 없는 탭이 있으면 Playwright 의 브라우저 전체 attach 가 통째로
타임아웃나기 때문 — `shared/cdp_page.py` 참고).

## 현재 상태

| 봇    | 사이트                      | 지역 마켓                     | 병렬 분할        | 마감 방식                              |
|-------|-----------------------------|-------------------------------|------------------|----------------------------------------|
| KKDAY | scm.kkday.com               | KOREA / JAPAN / AUSTRALIA / UK| 4분할 (quarter)  | All 체크 → Ceased selling → Confirm    |
| KLOOK | merchant.klook.com          | KOREA / JAPAN / AUSTRALIA / UK| 2분할 (fwd/bwd)  | -                                      |
| GG    | supplier.getyourguide.com   | KOREA / JAPAN / AUSTRALIA / UK| 분할 없음        | -                                      |
| VI    | supplier.viator.com         | GLOBAL                        | 4분할 (quarter)  | Availability → **Sold out** (4중 가드) |
| MRT   | partner.myrealtrip.com      | GLOBAL                        | 4분할 (quarter)  | -                                      |
| TPC   | vbooking.ctrip.com          | KOREA / JAPAN / AUSTRALIA     | 분할 없음        | On/off → Select All → Close sales → Set by Date → OK → **Submit** |

6개 봇 모두 구현 완료, 운영 중.

### TPC (Trip.com / Ctrip) — 2026-09-09 추가

지정 상품 10개(`tpc_targets.py`)만 매일 돈다. 두 가지가 다른 봇과 다르다.

**1) 이중검색으로 들어간다**
내부명칭으로 검색한 뒤, 그 결과 목록에서 지정된 상품번호 행을 골라 들어간다.
이름만으로 첫 행을 쓰거나(`경주` 로 검색하면 12건이 나온다) 번호로 상품 수정 URL 에
직행하면 안 된다. 둘이 서로를 검증한다. 하나라도 안 맞으면 **실패**로 남긴다.

**2) 화면 글자로 찾지 않는다**
2026-09-09 오후에 같은 계정 같은 Chrome 인데 vBooking UI 가 영어에서 중국어로 바뀌었다
(`Search`→`查询`, `OK`→`确 定`, `Not now`→`稍后处理`). 그래서 버튼·상태를 전부 구조로 찾는다 —
`.topSearchList button.ant-btn-primary`, `.ant-modal-footer` 의 primary/default,
라디오 `value`, `button[role=switch]` 의 `aria-checked`, `.date-detail.empty`.
`hub/tests/test_tpc.py` 가 글자 비교가 다시 들어오는지 검사한다.

**3) OK 는 반영이 아니다**
On/off 창의 OK 는 화면 안에서만 바꾼다. 화면 아래 **Submit** 까지 눌러야 서버에
들어가고, 반영에 시간이 좀 걸린다. 그래서 Submit 뒤에 **새로고침해서 서버가 준 값**으로
다시 읽어 검증한다.

```bash
python tpc.py --mode collect                  # 읽기 전용. 아무것도 바꾸지 않는다
python tpc.py --mode close --dry-run          # OK 를 누르지 않고 계획만
python tpc.py --mode close --date 2026-09-10
python tpc.py --mode open  --targets 경주
```

마감은 `Select All` 로 그 상품의 패키지를 전부 고른다. 그날 안 파는 패키지는
화면이 `Not set` 으로 두므로 손대지 않는다.

⚠️ **`Select All` 이 화면의 모든 패키지는 아니다.** 이름에 `Invalid` 가 붙은 패키지는
창 목록에 아예 안 나온다. 2026-09-09 감천미포가 화면 11개 / 창 10개였고, 빠진
`H-日文导游 (早班)` 가 하필 그날 유일하게 열려 있던 패키지였다. 그대로 Submit 하면
이미 닫힌 10개를 다시 닫고 '마감 완료' 모양만 남는다. 그래서 닫아야 할 패키지가 창에
없으면 OK 를 누르지 않고 `PKG_UNSELECTABLE` 실패로 멈춘다.

**오픈은 Select All 을 쓰지 않는다.** 그날 마감 로그(`logs/tpc_close_<날짜>_*.json`)에
남은 패키지만 되연다. '지금 닫혀 있는 것' 을 다 열면 시즌이 지나 닫아 둔 패키지까지
열린다 — 경주의 `A-中文导游 [十月 ~ 三月]` 는 9월에도 가격이 남은 채 닫혀 있다.
마감 기록이 없으면 열지 않고 `NO_CLOSE_LOG` 로 멈춘다.

호주 상품(Wollongong Kiama)은 한국 계정에 없다. 호주 Chrome(9524)이 떠 있어야 하고,
안 떠 있으면 스킵이 아니라 `CHROME_DOWN` **실패**로 남는다.

## 구조: 지역 = Chrome 인스턴스 1개

봇마다 Chrome 을 띄우는 게 아니라, **지역(마켓)마다 Chrome 1개**를 띄우고 그 안에 여러 OTA 사이트를 탭으로 연다.
한 지역 Chrome 을 여러 봇이 같이 attach 해서 쓴다. (단일 정의: `shared/health.py`)

| 지역      | 포트  | 런처                                | 열리는 사이트                            |
|-----------|-------|-------------------------------------|------------------------------------------|
| KOREA     | 9222  | `start_chrome_korea.bat`            | KLOOK + GG + KKDAY                       |
| JAPAN     | 9223  | `start_chrome_japan.bat`            | KLOOK + GG + KKDAY                       |
| AUSTRALIA | 9224  | `start_chrome_australia.bat`        | KLOOK + GG + KKDAY                       |
| UK        | 9225  | `start_chrome_uk.bat`               | KLOOK + GG + KKDAY                       |
| GLOBAL    | 9230  | `start_chrome_global.bat`           | Viator + MyRealTrip                      |

Chrome 프로필은 `%LOCALAPPDATA%\OTABot\chrome_<지역>` 에 지역별로 분리 저장된다 (세션/쿠키 유지).

## 폴더 구조

```
OTA Close/
├── gui.py                 # Tkinter GUI (평소 실행은 이걸로)
├── main.py                # 오케스트레이터 + 24/7 데몬 (지역별 시간표)
├── kkday.py               # KKDAY 봇
├── klook.py               # KLOOK 봇
├── gg.py                  # GetYourGuide 봇
├── vi.py                  # Viator 봇  ← Sold out 만 클릭 (안전규칙 필독)
├── mrt.py                 # MyRealTrip 봇
├── tpc.py                 # TPC(Trip.com) 봇  ← 마감 / 수집 / 오픈
├── tpc_dom.py             # TPC 화면 찾기 (선택자는 여기 한 벌만)
├── tpc_targets.py         # TPC 지정 상품 10개 (내부명칭 + 상품번호)
├── start_gui.bat          # GUI 실행
├── start_main.bat         # main.py 데몬 실행
├── start_all_chromes.bat  # 5개 지역 Chrome 한 번에 띄우기
├── requirements.txt
├── chrome_launchers/
│   ├── start_chrome_korea.bat      (9222)
│   ├── start_chrome_japan.bat      (9223)
│   ├── start_chrome_australia.bat  (9224)
│   ├── start_chrome_uk.bat         (9225)
│   └── start_chrome_global.bat     (9230)
├── shared/
│   ├── chrome_setup.py    # connect_and_setup(port)  (Playwright)
│   ├── cdp_page.py        # 페이지 타깃에 직접 붙기 (TPC 가 쓴다)
│   ├── health.py          # 지역↔포트↔봇 매핑, 포트 헬스체크 + .bat 자동 부팅
│   ├── logger.py          # 통합 로거
│   ├── notify.py          # 결과 통지 (stdout + summary 파일)
│   └── types.py           # Result TypedDict
└── logs/
    ├── ota_close_bot_YYYY-MM-DD.log   # 전체 실행 로그
    ├── summary_YYYY-MM-DD.txt         # 일일 결과 요약
    ├── kkday_summary_YYYY-MM-DD.txt   # KKDAY 상품별 상세
    ├── discover/                      # discover-once 결과 JSON
    └── queue/                         # work-stealing claim 마커
```

## 설치 (한 번만)

```powershell
cd "이 폴더"
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

## 처음 한 번: 지역별 수동 로그인

봇은 각 포트에 떠 있는 Chrome 의 **프로필/쿠키**를 그대로 쓴다. 지역 Chrome 마다 한 번씩 직접 로그인해서 세션을 박아둬야 한다.

```powershell
start_all_chromes.bat      # 5개 지역 Chrome 이 사이트 탭까지 열어줌
```

각 창에서 열린 사이트에 로그인 (GG 는 6자리 TOTP, "Remember me" 체크). 로그인 후 Chrome 창은 **닫지 말 것** (최소화는 OK). 봇이 그 포트에 attach 한다.

## 실행

### GUI (평소 사용)

```powershell
start_gui.bat        # 또는 python gui.py
```

- **Date**: 기본값 = 내일. Today / Tomorrow / +2d 버튼
- **Agencies**: 실행할 OTA 체크 (초기 전부 OFF — 직접 선택)
- **Chrome**: 선택한 Agency 에 필요한 지역이 **자동 체크**됨 (KKDAY 고르면 KOREA/JAPAN/AUSTRALIA/UK, VI 고르면 GLOBAL)
- **RUN**: 필요한 Chrome 만 부팅 후 실행. 로그는 ALL / 봇별 탭으로 분리 출력
- GUI 는 항상 **실제 실행**이다 (dry-run 없음)

### CLI

```powershell
python main.py --once --dry-run                 # 실제 클릭 없이 대상만 확인
python main.py --once --agency vi,kkday         # 특정 봇만
python main.py --once --regions AUSTRALIA       # 특정 지역 마켓만
python main.py --once --date 2026-07-20         # 특정 날짜 마감
python main.py --once                           # 전 봇 즉시 1회 (운영)
```

### 24/7 데몬 (지역별 시간표)

```powershell
python main.py       # 또는 start_main.bat
```

시간표는 [main.py](main.py) 의 `SCHEDULE` 에서 수정한다.

| 시각  | 지역 마켓        | 글로벌 봇(VI/MRT) 동시 실행 |
|-------|------------------|------------------------------|
| 08:50 | AUSTRALIA        | X                            |
| 09:50 | KOREA, JAPAN     | O                            |
| (미정)| UK               | 주석 처리됨 — 시간 확정 후 활성화 |

종료는 Ctrl+C. 작업 스케줄러/NSSM 으로 서비스화하면 PC 부팅 시 자동 기동.

### 봇 개별 실행 (디버깅)

```powershell
python vi.py --dry-run --date 2026-07-20
python kkday.py --dry-run --region KOREA --quarter 1
```

## 마감 날짜 정책

- 기본: **PC 시간 기준 내일** (`datetime.now() + 1day`)
- 09:50 에 내일을 마감 = 다음날 신규 예약 차단
- `--date YYYY-MM-DD` 로 특정 날짜 지정 가능

## 병렬 실행 구조 (main.py)

1. **Discover-once-share**: main 이 먼저 `--mode discover` 로 상품 목록을 1번만 긁어 JSON 으로 저장 (`logs/discover/`) → worker 들에 `--discover-file` 로 전달. worker 들이 같은 목록을 중복으로 안 긁는다.
2. **Work stealing**: `logs/queue/` 의 claim 마커를 worker 들이 원자적으로 선점 → 빠른 worker 가 남은 일을 가져간다 (idle worker 없음).
3. **분할**: KKDAY/VI/MRT 는 quarter 1~4 로 4분할, KLOOK 은 forward/backward 2분할, GG 는 분할 없음. 지역 봇은 지역별로도 병렬.
4. 각 worker 는 subprocess. stdout 을 스레드로 실시간 스트리밍 → 봇 단위로 결과 집계.

## ⚠️ Viator 절대 안전 규칙

- VI 봇은 **오직 "Sold out" 만** 클릭한다. **"Not operating" 은 cancellation 의미**(기존 예약까지 취소될 위험)라 절대 사용 금지.
- 코드 4중 안전 가드 ([vi.py](vi.py)):
  1. 셀렉터로 `textContent === 'Sold out'` 만 매칭
  2. 클릭 직전 `textContent.strip() == 'Sold out'` 재검증 → 다르면 `RuntimeError`
  3. `'operating'` 문자열이 한 글자라도 있으면 `RuntimeError` 던지고 중단
  4. 클릭 대상이 target 날짜 섹션 **외부**면 `RuntimeError`

## 로그 읽는 법

- `logs/summary_YYYY-MM-DD.txt` — 봇별 성공/실패/스킵 + 실패 사유
- `logs/kkday_summary_YYYY-MM-DD.txt` — KKDAY 상품별 상세 (실패 상품 URL 포함)
- `logs/ota_close_bot_YYYY-MM-DD.log` — 전체 실행 로그

### KKDAY `CONFIRM_FAILED` 가 떴다면

"Ceased selling" 은 눌렸는데 그 다음 **Confirm 모달을 약 9초 안에 못 찾은** 경우다 (모달 뜨는 타이밍 이슈, 상품 문제 아님).

봇은 실행 끝에 fresh page 로 1번 재시도하는데, 재시도에서 열린 행이 0개(=이미 닫힘)로 나와도 **일부러 성공으로 인정하지 않는다**. Confirm 실패 후의 0행은 "진짜 닫힘"과 "렌더 지연으로 0행 오독"을 구분할 수 없어서, 열린 채로 성공 위장하는 것보다 사람이 확인하는 게 안전하기 때문 ([kkday.py](kkday.py) 의 `SAW_OPEN_ROWS` 가드).

**즉 `CONFIRM_FAILED` = "실패 확정"이 아니라 "수동 확인 필요"다.** 실제로는 마감된 경우가 많다. summary 에 찍힌 URL 로 들어가서 해당 날짜가 닫혔는지 눈으로 확인하면 된다.

주의: 로그의 `rows_after` 값은 실패 시 **재측정 없이 `rows_before` 를 그대로 복사**한 값이라 믿으면 안 된다.

## 트러블슈팅

**Chrome 포트가 죽었어요 / connect 실패**
→ `start_all_chromes.bat` 또는 해당 지역 `chrome_launchers\start_chrome_<지역>.bat` 재실행. `main.py`/GUI 도 자동으로 한 번 부팅 시도한다.

**Viator 에서 날짜 선택이 하루 밀려요**
→ 봇이 picker 값을 검증해서 불일치 시 재클릭, 그래도 안 되면 새로고침 후 재시도한다.

**로그에 한글이 깨져요**
→ 로그 파일은 UTF-8. PowerShell 로 볼 땐 `Get-Content <파일> -Encoding UTF8`.

## TODO

- [ ] UK 마감 시간 확정 → `main.py` 의 `SCHEDULE` 에서 UK 슬롯 주석 해제
- [ ] KKDAY `CONFIRM_FAILED` 시 Search 재클릭 + 열린 행 재측정 → "진짜 닫힘 / 못 닫음" 자동 구분 (현재는 수동 확인)
- [ ] 결과 통지: 현재 stdout + 파일뿐. 카카오톡/이메일 연동은 `shared/notify.py` 에 추가
- [ ] 루트의 `zieLLEXV`, `ziYFauMf` (확장자 없는 63KB ZIP 백업, 출처 불명) 정리 여부 결정
