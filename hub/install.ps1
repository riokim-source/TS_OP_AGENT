# TOURSTORY OP SYSTEM - 설치 확인
#
# 이 스크립트는 '이 PC 에서 돌 준비가 됐는지' 를 확인하고 부족한 것만 채운다.
# 폴더를 통째로 복사해 온 뒤 한 번 실행하면 된다.
#
# 로그인은 각자 알아서 한다. 이 스크립트는 계정을 건드리지 않는다.

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

function Line { param($t) Write-Host $t }
function Ok   { param($t) Write-Host "  [OK]   $t" -ForegroundColor Green }
function Warn { param($t) Write-Host "  [확인] $t" -ForegroundColor Yellow }
function Bad  { param($t) Write-Host "  [문제] $t" -ForegroundColor Red }

Line ("=" * 70)
Line " TOURSTORY OP SYSTEM - 설치"
Line ("=" * 70)
Line "  폴더: $Root"
Line ""

# ── 1) Python ────────────────────────────────────────────────────────────
Line "[1/5] Python"
$py = $null
foreach ($c in @("python", "py")) {
    try {
        $v = & $c -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $v) { $py = $c; break }
    } catch {}
}
if (-not $py) {
    Bad "Python 이 없습니다."
    Line "       https://www.python.org/downloads/ 에서 3.10 이상을 설치하세요."
    Line "       설치할 때 'Add python.exe to PATH' 를 반드시 체크하세요."
    exit 1
}
$ver = & $py -c "import sys; print('%d.%d' % sys.version_info[:2])"
$major, $minor = $ver.Split('.')
if ([int]$major -lt 3 -or ([int]$major -eq 3 -and [int]$minor -lt 10)) {
    Bad "Python $ver 은 너무 낮습니다. 3.10 이상이 필요합니다."
    exit 1
}
Ok "Python $ver"

# ── 2) 패키지 ────────────────────────────────────────────────────────────
Line ""
Line "[2/5] 필요한 패키지"
$req = Join-Path $Root "requirements.txt"
if (-not (Test-Path $req)) { Bad "requirements.txt 가 없습니다."; exit 1 }
& $py -m pip install --disable-pip-version-check -q -r $req
if ($LASTEXITCODE -ne 0) {
    Bad "패키지 설치 실패. 위 메시지를 확인하세요."
    exit 1
}
foreach ($m in @("playwright", "pandas", "openpyxl")) {
    $has = & $py -c "import importlib.util as u; print(1 if u.find_spec('$m') else 0)"
    if ($has -eq "1") { Ok $m } else { Bad "$m 설치 안 됨" }
}

# ── 3) Chrome ────────────────────────────────────────────────────────────
Line ""
Line "[3/5] Chrome"
$found = & $py -c @"
import sys, os
sys.path.insert(0, r'$Root\hub')
from core.routing import Routing
p = Routing.find_chrome()
print(p or '')
"@
if ($found) { Ok "Chrome: $found" }
else {
    Bad "Chrome 을 찾지 못했습니다. https://www.google.com/chrome/ 에서 설치하세요."
}

# ── 4) 봇 폴더 ───────────────────────────────────────────────────────────
Line ""
Line "[4/5] 봇 폴더"
foreach ($d in @(@("Klook Open", "packages.py"), @("OTA Close", "kkday.py"))) {
    $p = Join-Path $Root $d[0]
    if (Test-Path (Join-Path $p $d[1])) { Ok $d[0] }
    else { Bad "$($d[0]) 폴더가 없거나 $($d[1]) 이 빠졌습니다." }
}

# ── 5) 중계 열쇠 ─────────────────────────────────────────────────────────
# 이게 없으면 Agent 가 켜지자마자 꺼진다. 팀원이 가장 많이 걸리는 지점이라
# 설치할 때 미리 잡아 준다.
Line ""
Line "[5/5] 중계 열쇠"
$key = Join-Path $Root "hub\\data\\firebase_service_account.json"
if (Test-Path $key) {
    $proj = ""
    try { $proj = (Get-Content $key -Raw | ConvertFrom-Json).project_id } catch { }
    $dburl = ""
    try { $dburl = (Get-Content $key -Raw | ConvertFrom-Json).database_url } catch { }
    if (-not $dburl) {
        $cfg = Join-Path $Root "hub\data\firebase.json"
        if (Test-Path $cfg) {
            try { $dburl = (Get-Content $cfg -Raw | ConvertFrom-Json).database_url } catch { }
        }
    }
    if (-not $proj) { Bad "열쇠 파일이 깨졌습니다. 다시 받으세요." }
    elseif (-not $dburl) {
        # 주소가 없으면 열쇠가 있어도 Agent 가 붙지 못한다.
        # 화면에는 '열쇠 있음' 만 뜨고 왜 안 되는지 알 수 없어서 여기서 잡는다.
        Bad "중계 주소가 없습니다 (database_url)"
        Line "       열쇠를 화면에서 다시 받으세요. 주소가 함께 들어 있습니다."
    }
    else { Ok "열쇠 있음 ($proj)" }
} else {
    Bad "열쇠가 없습니다 -> hub\data\firebase_service_account.json"
    Line "       관리자에게 요청하세요. 사내 공유 폴더나 USB 로만 받습니다."
    Line "       (메신저나 메일로 주고받지 마세요)"
}

# 다른 PC 에서 복사해 온 경우에만 개인 파일을 정리한다.
#
# 접속 토큰은 한번 정하면 그대로 둔다 -- 폰 즐겨찾기에 ?k=... 가 들어 있어서
# 매번 새로 만들면 링크가 죽는다. 그래서 '설치 주인' 을 기록해 두고,
# 그게 이 PC 와 다를 때만(= 남의 폴더를 복사해 왔을 때만) 지운다.
$marker = Join-Path $Root "hub\data\install.json"
$me = "$env:COMPUTERNAME/$env:USERNAME"
$owner = ""
if (Test-Path $marker) {
    try { $owner = (Get-Content $marker -Raw | ConvertFrom-Json).owner } catch { $owner = "" }
}
if ($owner -ne $me) {
    $stale = @("hub\logs\_token.txt", "hub\data\last_upload.bin", "hub\data\last_upload.json")
    $cleaned = @()
    foreach ($f in $stale) {
        $p = Join-Path $Root $f
        if (Test-Path $p) { Remove-Item $p -Force; $cleaned += $f }
    }
    New-Item -ItemType Directory -Force -Path (Split-Path $marker) | Out-Null
    (@{owner = $me; installed = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")} | ConvertTo-Json) |
        Out-File -FilePath $marker -Encoding utf8
    Line ""
    if ($owner -eq "") { Ok "이 PC 용으로 설치했습니다 ($me)" }
    else {
        Warn "다른 PC($owner) 의 폴더를 가져왔습니다 -> 이 PC($me) 용으로 초기화"
    }
    if ($cleaned.Count -gt 0) {
        Line "       개인 파일 정리: $($cleaned -join ', ')"
        Line "       (접속 토큰과 마지막 업로드 파일은 PC 마다 새로 만들어집니다)"
    }
} else {
    Line ""
    Ok "이미 이 PC 용으로 설치돼 있습니다 - 접속 토큰과 업로드 파일은 그대로 둡니다"
}

Line ""
Line ("=" * 70)
Line " 다음 순서"
Line ("=" * 70)
Line "  1. 'Agent 켜기.bat' 을 실행합니다. (검은 창은 닫지 마세요)"
Line "  2. 화면 주소를 열고 [Chrome 로그인] 에서 프로필을 하나씩 '실행' 합니다."
Line "  3. 열린 Chrome 창에서 각 OTA 에 로그인합니다."
Line "       KR/JP/AU  ... Klook, KKday, GetYourGuide, Trip.com"
Line "       GLOBAL     ... Viator, MyRealTrip"
Line "  4. [전체 확인] 버튼으로 전부 '로그인됨' 인지 봅니다."
Line ""
Line "  로그인은 각 Chrome 프로필에 저장되어 다음부터는 다시 안 해도 됩니다."
Line ""
Line "  화면은 인터넷 주소 하나로 다 같이 씁니다. 설치할 것이 없습니다."
Line "  주소는 '설치안내.md' 에 있습니다. 즐겨찾기에 넣어 두세요."
Line ""
Line "  매일 아침 할 일은 'Agent 켜기.bat' 하나뿐입니다."
Line ("=" * 70)
