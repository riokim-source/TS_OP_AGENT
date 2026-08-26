# 모바일에서 Klook Inventory Open 실행하기

## 0. 먼저 알아야 할 것

봇 자체는 **반드시 이 PC 에서** 돌아야 합니다.
`klook_worker.py` 는 Playwright 로 **이 PC 에 떠 있는 Chrome 의 CDP 포트(9522~9525)** 에 붙어서
이미 로그인된 Klook Merchant Center 세션을 조작하기 때문입니다.

따라서 "모바일 실행" 은 실제로는 **휴대폰이 PC 의 봇을 원격 조작하는 리모컨** 입니다.

```
[휴대폰 브라우저]  ──HTTP──▶  [PC: mobile_server.py]  ──subprocess──▶  [klook_worker.py]
                                                          │
                                                          └──CDP──▶ [PC: Chrome 9522~9525]
```

준비물은 항상 동일합니다.

1. PC 가 켜져 있고 절전 상태가 아닐 것
2. 각 지사 Chrome 이 remote-debugging 포트로 떠 있고 **Klook Merchant Center 에 로그인** 되어 있을 것
   (`start_chrome_korea.bat` 등 — 모바일 화면의 지사 칩을 눌러서 원격으로 띄울 수도 있습니다)
3. `start_mobile.bat` 이 실행 중일 것 (창을 닫으면 리모컨도 끊깁니다)

---

## 1. 서버 켜기 (PC)

`start_mobile.bat` 더블클릭. 또는:

```bash
python mobile_server.py
```

콘솔에 이런 링크가 뜹니다.

```
이 PC:      http://localhost:8500/?k=bbocHaPaOohuKyLZ
같은 WiFi:  http://172.30.1.37:8500/?k=bbocHaPaOohuKyLZ
```

`?k=...` 는 접속 토큰입니다. **이 링크를 아는 사람은 실제 인벤토리를 열 수 있으니** 외부에 공유하지 마세요.
토큰은 `logs/_mobile_token.txt` 에 저장되어 재시작해도 그대로라, 휴대폰 즐겨찾기가 계속 동작합니다.
토큰을 바꾸고 싶으면 그 파일을 지우고 서버를 다시 켜면 됩니다.

---

## 2. 방법 A — 같은 Wi-Fi (사무실/집, 가장 간단)

1. PC 와 휴대폰이 **같은 공유기**에 연결되어 있어야 합니다.
2. Windows 방화벽에서 포트를 한 번만 열어줍니다. **PowerShell 관리자 권한**으로:

```powershell
New-NetFirewallRule -DisplayName "Klook Mobile 8500" -Direction Inbound -Protocol TCP -LocalPort 8500 -Action Allow -Profile Private
```

> `-Profile Private` 로 제한하는 것이 중요합니다. 공용 네트워크 프로필에서는 열지 마세요.

3. 휴대폰 브라우저에서 콘솔에 뜬 `같은 WiFi:` 링크를 엽니다.
4. 한 번 열면 토큰이 쿠키에 저장되므로, 이후에는 `http://172.30.1.37:8500` 만으로도 들어갑니다.
5. Safari `공유 → 홈 화면에 추가` / Chrome `⋮ → 홈 화면에 추가` 하면 앱처럼 전체화면으로 뜹니다.

**단점:** 공유기 IP(`172.30.1.37`)는 재부팅 시 바뀔 수 있습니다.
공유기 설정에서 이 PC 를 **DHCP 고정 IP** 로 잡아두면 링크가 고정됩니다.

---

## 3. 방법 B — 외부에서 (Tailscale, 권장)

출근길/외부에서 열어야 하면 이 방법이 가장 안전합니다. 포트를 인터넷에 노출하지 않고,
PC 와 휴대폰을 같은 가상 사설망에 넣습니다.

1. PC: <https://tailscale.com/download/windows> 설치 → 로그인
2. 휴대폰: App Store / Play 스토어에서 `Tailscale` 설치 → **같은 계정**으로 로그인
3. PC 의 Tailscale IP 확인 (`100.x.x.x`):

```powershell
tailscale ip -4
```

4. 휴대폰에서 접속: `http://100.x.x.x:8500/?k=<토큰>`

Tailscale VPN 이 켜져 있는 동안 LTE/5G 에서도 그대로 동작합니다. 방화벽 규칙도 대부분 불필요합니다.
(안 되면 위 방화벽 명령을 `-Profile Private` 대신 `-Profile Any` 로 한 번 실행)

**MagicDNS** 를 켜면 IP 대신 `http://<PC이름>:8500` 으로 접속할 수 있어 더 편합니다.

---

## 4. 방법 C — Cloudflare Tunnel (임시 공개 URL)

Tailscale 설치가 곤란할 때 쓰는 임시 방법입니다.

```bash
winget install --id Cloudflare.cloudflared
cloudflared tunnel --url http://localhost:8500
```

`https://xxxx-yyyy.trycloudflare.com` 형태의 임시 주소가 발급됩니다.
거기에 `/?k=<토큰>` 을 붙여 휴대폰에서 접속하세요.

⚠️ **이 주소는 인터넷 전체에 열립니다.** 토큰이 유일한 방어선이므로:
- 쓸 때만 켜고 끝나면 즉시 `Ctrl+C`
- 주소를 메신저 등에 남기지 말 것
- 상시 운영에는 쓰지 말 것 (그 용도는 방법 B)

---

## 5. 방법 D — 원격 데스크톱 (코드 없이 데스크톱 GUI 그대로)

`gui.py` 화면을 그대로 휴대폰에서 보고 조작하는 방식입니다.
설정이 가장 단순하지만, 화면이 작아서 터치 조작은 불편합니다.

- **Chrome Remote Desktop** — <https://remotedesktop.google.com/access> (PC 설정 → 폰 앱 설치, 무료, 계정만 있으면 됨)
- **RustDesk** — 오픈소스, 자체 서버 구성 가능

Chrome 창 상태를 눈으로 직접 확인해야 하는 상황(로그인 만료, 캡차, 알 수 없는 팝업)에서는
이 방법이 오히려 유용하므로, **방법 A/B 와 함께 깔아두는 것을 권장**합니다.
리모컨(웹)으로 실행 → 뭔가 이상하면 원격 데스크톱으로 들어가서 직접 확인.

---

## 6. PC 절전 방지 (중요)

PC 가 잠들면 리모컨도 봇도 죽습니다. 관리자 PowerShell 에서:

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
```

모니터만 끄는 것(`monitor-timeout-ac`)은 괜찮습니다. Chrome 실행 `.bat` 에 이미
`--disable-background-timer-throttling` 등이 들어 있어 창이 뒤에 있어도 동작합니다.

---

## 7. 모바일 화면 사용법

| 영역 | 하는 일 |
|---|---|
| 상단 칩 (`● Korea :9522`) | 지사별 Chrome 연결 상태. **초록 ●** = 연결됨, **회색 ○** = 미연결. 탭하면 해당 지사 Chrome 을 PC 에서 실행 |
| 대상 날짜 | `내일` (기본) / `특정 날짜` (네이티브 날짜 선택기) |
| 작업 목록 | `상품명 수량, 상품명 수량`. CLI 와 완전히 동일한 형식 |
| 상품 찾기 | 이름/ID 검색 → 수량 입력 → `추가` 로 위 입력창에 자동 삽입 |
| 미리보기 | 실행 전에 지사 분류 / Package·Activity 방식 / 매핑 실패 항목 확인 |
| ▶ 실행 | 확인 팝업 후 실행. 지사별 worker 가 **병렬**로 돌아감 |
| 진행 상황 | 진행바 + 항목별 결과가 실시간 갱신. `실시간 로그` 를 펼치면 worker stdout 그대로 |
| ■ 중단 | 실행 중인 worker 종료 (진행 중이던 항목은 실패로 기록) |
| 최근 기록 | `logs/booking_log_KLOOK_*.txt` 최근 120줄 — 재시도 대상 확인용 |

**수량 0** 을 넣으면 CLI 와 동일하게 `Inventory 0 + Activate OFF` (마감) 처리됩니다.

---

## 8. 문제 해결

| 증상 | 원인 / 조치 |
|---|---|
| 휴대폰에서 페이지가 안 열림 | 방화벽 규칙(2절) 확인 / PC 와 폰이 같은 Wi-Fi 인지 / `start_mobile.bat` 창이 살아 있는지 |
| `토큰이 필요합니다` | `?k=<토큰>` 붙은 전체 링크로 다시 접속. 토큰은 `logs/_mobile_token.txt` |
| 지사 칩이 계속 회색 ○ | 해당 지사 Chrome 이 안 떠 있음. 칩을 탭해서 실행 후 **PC 에서 로그인 상태 확인** |
| 실행했는데 전부 `실패` + 메모가 `worker 가 결과를 남기지 않음` | Chrome 미연결이거나 로그인이 풀림. 원격 데스크톱(방법 D)으로 들어가 Merchant Center 로그인 확인 |
| `이미 실행 중입니다` | 리모컨은 동시 1건만 허용. 끝날 때까지 기다리거나 `■ 중단` |
| 폰과 PC 에서 동시에 조작 | 마지막 요청이 이깁니다. 한 사람만 조작하세요 |
