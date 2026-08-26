# -*- coding: utf-8 -*-
"""
mobile_server.py
Klook Inventory Open — 모바일/원격용 웹 GUI.

봇 자체는 반드시 이 PC 에서 돌아야 한다 (Playwright 가 로컬 Chrome 의 CDP 포트에 붙기 때문).
그래서 이 서버는 "휴대폰이 PC 의 봇을 원격으로 조작하는 리모컨" 이다.
실행방식은 gui.py / main.py 와 완전히 동일한 klook_core.Runner 를 쓴다.

실행:
  python mobile_server.py            (기본 포트 8500)
  python mobile_server.py 9000       (포트 지정)

접속:
  같은 Wi-Fi:  http://<PC-IP>:8500/?k=<토큰>
  외부:        Tailscale / Cloudflare Tunnel 로 이 포트를 노출 (README_MOBILE.md 참고)

보안:
  - 토큰(logs/_mobile_token.txt)이 있어야 접속 가능. 최초 실행 시 자동 생성.
  - 토큰은 쿠키에 저장되므로 휴대폰에서 한 번만 링크로 열면 이후 즐겨찾기로 접속 가능.
  - 실제 예약 인벤토리를 여는 도구이므로 공용 Wi-Fi 에서 포트를 그냥 열지 말 것.
"""
from __future__ import annotations

import json
import secrets
import sys
import threading
import urllib.parse
from datetime import datetime
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import klook_core as core

DEFAULT_PORT = 8500
TOKEN_FILE = core.LOG_DIR / "_mobile_token.txt"
MAX_LOG_LINES = 4000


def get_token() -> str:
    if TOKEN_FILE.exists():
        tok = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    tok = secrets.token_urlsafe(12)
    TOKEN_FILE.write_text(tok, encoding="utf-8")
    return tok


TOKEN = get_token()


# ──────────────────────────────────────────────────────────────────────────────
# 세션 상태 (한 번에 한 실행만)
# ──────────────────────────────────────────────────────────────────────────────
class Session:
    def __init__(self):
        self.lock = threading.Lock()
        self.runner: core.Runner | None = None
        self.logs: list[dict] = []
        self.results: list[dict] = []
        self.summary: dict | None = None
        self.total = 0
        self.started_text = ""

    def reset(self, total: int):
        with self.lock:
            self.logs = []
            self.results = []
            self.summary = None
            self.total = total
            self.started_text = datetime.now().strftime("%H:%M:%S")

    def add_log(self, region, line):
        with self.lock:
            self.logs.append({
                "region": "SYS" if region == "*" else core.REGION_DISPLAY.get(region, region),
                "line": line,
            })
            if len(self.logs) > MAX_LOG_LINES:
                del self.logs[:len(self.logs) - MAX_LOG_LINES]

    def add_result(self, region, res):
        result = str(res.get("result", "")).strip()
        if str(res.get("workflow", "")).lower() == "activity" and result == "실패":
            result = "새버전 실패"
        with self.lock:
            self.results.append({
                "item": core.cli.item_text_of(res),
                "region": core.REGION_DISPLAY.get(region, region),
                "result": result,
                "memo": str(res.get("memo", "")).strip()[:300],
            })

    def set_summary(self, summary):
        with self.lock:
            self.summary = {
                "duration": summary["duration_text"],
                "failed": summary["failed"],
                "stopped": summary["stopped"],
                "regions": [
                    {
                        "display": d["display"],
                        "groups": {k: v for k, v in d["groups"].items()},
                    }
                    for d in summary["regions"].values()
                ],
            }

    def snapshot(self, since: int):
        with self.lock:
            return {
                "running": bool(self.runner and self.runner.running),
                "since": len(self.logs),
                "logs": self.logs[since:] if since < len(self.logs) else [],
                "results": self.results,
                "total": self.total,
                "summary": self.summary,
                "started": self.started_text,
            }


SESSION = Session()


# ──────────────────────────────────────────────────────────────────────────────
# API 핸들러
# ──────────────────────────────────────────────────────────────────────────────
def api_preview(body: dict) -> dict:
    text = body.get("text", "")
    try:
        target_date = core.normalize_date(body.get("date", ""))
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    parsed = core.parse_input(text)
    return {
        "ok": True,
        "date_text": core.describe_date(target_date),
        "total": parsed["total"],
        "regions": [
            {
                "region": r,
                "display": core.REGION_DISPLAY[r],
                "tasks": [
                    {"name": t["name"], "workflow": t["workflow"],
                     "id": t["package_id"], "inventory": t["inventory"]}
                    for t in parsed["region_tasks"][r]
                ],
            }
            for r in core.SUPPORTED_REGIONS if parsed["region_tasks"].get(r)
        ],
        "unknown": [{"text": u.get("input_text"), "memo": u.get("memo")} for u in parsed["unknown"]],
        "warnings": parsed["warnings"],
    }


def api_run(body: dict) -> dict:
    if SESSION.runner and SESSION.runner.running:
        return {"ok": False, "error": "이미 실행 중입니다."}
    try:
        target_date = core.normalize_date(body.get("date", ""))
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    parsed = core.parse_input(body.get("text", ""))
    if parsed["total"] == 0:
        return {"ok": False, "error": "실행할 작업이 없습니다. (매핑된 상품 없음)"}

    SESSION.reset(parsed["total"])
    runner = core.Runner(
        parsed["region_tasks"], parsed["unknown"], target_date,
        on_log=SESSION.add_log,
        on_result=SESSION.add_result,
        on_done=SESSION.set_summary,
    )
    SESSION.runner = runner
    try:
        runner.start()
    except Exception as e:
        return {"ok": False, "error": f"실행 실패: {e}"}
    return {"ok": True, "total": parsed["total"], "date_text": core.describe_date(target_date)}


def api_stop(body: dict) -> dict:
    if SESSION.runner and SESSION.runner.running:
        SESSION.runner.stop()
        return {"ok": True}
    return {"ok": False, "error": "실행 중이 아닙니다."}


def api_status(_body=None) -> dict:
    st = core.cdp_status_all()
    return {"ok": True, "regions": [
        {"region": r, "display": core.REGION_DISPLAY[r], "port": st[r]["port"], "connected": st[r]["ok"]}
        for r in core.SUPPORTED_REGIONS
    ]}


def api_chrome(body: dict) -> dict:
    region = str(body.get("region", "")).upper()
    if region not in core.SUPPORTED_REGIONS:
        return {"ok": False, "error": "알 수 없는 지사"}
    try:
        return {"ok": True, "message": core.launch_chrome(region)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def api_catalog(_body=None) -> dict:
    return {"ok": True, "items": core.product_catalog(),
            "variants": {k: v for k, v in core.variant_groups().items()}}


def api_history(query: dict) -> dict:
    region = (query.get("region", ["KOREA"])[0] or "KOREA").upper()
    if region not in core.SUPPORTED_REGIONS:
        region = "KOREA"
    return {"ok": True, "region": region, "lines": core.tail_log(region, 120)}


# ──────────────────────────────────────────────────────────────────────────────
# HTTP
# ──────────────────────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    server_version = "KlookMobile/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # 콘솔 스팸 방지

    # ── auth ──
    def _authed(self, query: dict) -> bool:
        if query.get("k", [None])[0] == TOKEN:
            return True
        if self.headers.get("X-Token") == TOKEN:
            return True
        raw = self.headers.get("Cookie")
        if raw:
            try:
                morsel = SimpleCookie(raw).get("klook_k")
                return bool(morsel) and morsel.value == TOKEN
            except Exception:
                return False
        return False

    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8", cookie=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"))

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    # ── routes ──
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            if query.get("k", [None])[0] == TOKEN:
                cookie = f"klook_k={TOKEN}; Path=/; Max-Age=31536000; SameSite=Lax"
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8", cookie)
                return
            if self._authed(query):
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
                return
            self._send(401, "<h3>토큰이 필요합니다. PC 콘솔에 출력된 링크로 접속하세요.</h3>".encode("utf-8"),
                       "text/html; charset=utf-8")
            return

        if not self._authed(query):
            self._json({"ok": False, "error": "unauthorized"}, 401)
            return

        if path == "/api/state":
            since = int((query.get("since", ["0"])[0]) or 0)
            self._json(SESSION.snapshot(since))
        elif path == "/api/catalog":
            self._json(api_catalog())
        elif path == "/api/status":
            self._json(api_status())
        elif path == "/api/history":
            self._json(api_history(query))
        else:
            self._json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if not self._authed(query):
            self._json({"ok": False, "error": "unauthorized"}, 401)
            return
        body = self._read_body()
        routes = {
            "/api/preview": api_preview,
            "/api/run": api_run,
            "/api/stop": api_stop,
            "/api/chrome": api_chrome,
        }
        fn = routes.get(parsed.path)
        if not fn:
            self._json({"ok": False, "error": "not found"}, 404)
            return
        try:
            self._json(fn(body))
        except Exception as e:
            self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)


# ──────────────────────────────────────────────────────────────────────────────
# 모바일 페이지
# ──────────────────────────────────────────────────────────────────────────────
PAGE = r"""<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#0d1117">
<title>Klook Open</title>
<style>
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:#0d1117;color:#c9d1d9;font:15px/1.5 -apple-system,"Malgun Gothic",sans-serif;padding:0 0 96px}
header{position:sticky;top:0;z-index:10;background:#161b22;border-bottom:1px solid #30363d;padding:10px 14px}
h1{font-size:16px;margin:0 0 8px}
.chips{display:flex;gap:6px;overflow-x:auto;padding-bottom:2px}
.chip{flex:0 0 auto;font-size:12px;padding:5px 9px;border-radius:999px;border:1px solid #30363d;background:#0d1117;color:#8b949e}
.chip.on{border-color:#238636;color:#7ee787}
section{padding:14px;border-bottom:1px solid #21262d}
label{display:block;font-size:12px;color:#8b949e;margin-bottom:6px;letter-spacing:.02em}
textarea,input,select{width:100%;background:#0d1117;color:#e6edf3;border:1px solid #30363d;border-radius:10px;padding:11px;font:15px/1.5 inherit}
textarea{min-height:110px;resize:vertical}
.row{display:flex;gap:8px}
.row>*{flex:1}
button{border:0;border-radius:10px;padding:13px;font-size:15px;font-weight:600;background:#21262d;color:#c9d1d9}
button:active{opacity:.7}
button.primary{background:#238636;color:#fff}
button.danger{background:#da3633;color:#fff}
button.ghost{background:#0d1117;border:1px solid #30363d}
button[disabled]{opacity:.4}
.seg{display:flex;border:1px solid #30363d;border-radius:10px;overflow:hidden;margin-bottom:8px}
.seg button{flex:1;border-radius:0;background:#0d1117;font-weight:500}
.seg button.on{background:#1f6feb;color:#fff}
.hint{font-size:12px;color:#8b949e;margin-top:6px}
.box{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:10px;font-size:13px;max-height:230px;overflow:auto}
pre.log{background:#010409;border:1px solid #30363d;border-radius:10px;padding:10px;font:12px/1.45 ui-monospace,Consolas,monospace;
        max-height:300px;overflow:auto;white-space:pre-wrap;word-break:break-all;margin:0}
.r{padding:7px 0;border-bottom:1px solid #21262d;font-size:13px}
.r:last-child{border:0}
.b{font-weight:600}
.g{color:#7ee787}.rd{color:#ff7b72}.o{color:#e3b341}.d{color:#8b949e}.p{color:#d2a8ff}.bl{color:#79c0ff}
.list{max-height:260px;overflow:auto;border:1px solid #30363d;border-radius:10px;margin-top:8px}
.item{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:10px 11px;border-bottom:1px solid #21262d}
.item:last-child{border:0}
.item small{color:#8b949e;font-size:11px}
.item button{padding:7px 12px;font-size:13px;flex:0 0 auto}
.bar{position:fixed;left:0;right:0;bottom:0;background:#161b22;border-top:1px solid #30363d;
     padding:10px 14px calc(10px + env(safe-area-inset-bottom));display:flex;gap:8px;z-index:20}
.bar button{flex:1}
.prog{height:4px;background:#21262d;border-radius:3px;overflow:hidden;margin-top:8px}
.prog>i{display:block;height:100%;background:#238636;width:0;transition:width .3s}
details summary{cursor:pointer;color:#8b949e;font-size:13px;padding:4px 0}
</style></head><body>

<header>
  <h1>Klook Inventory Open</h1>
  <div class="chips" id="chips"></div>
</header>

<section>
  <label>대상 날짜</label>
  <div class="seg">
    <button id="dTom" class="on" onclick="setMode('t')">내일</button>
    <button id="dCus" onclick="setMode('c')">특정 날짜</button>
  </div>
  <input type="date" id="date" style="display:none">
  <div class="hint" id="dateHint"></div>
</section>

<section>
  <label>작업 목록 — 상품명 수량, 상품명 수량</label>
  <textarea id="tasks" placeholder="남쁘 5, Toyako Niseko 2" autocapitalize="off" autocorrect="off"></textarea>
  <div class="hint">수량 0 → Inventory 0 + Activate OFF (마감)</div>
  <div class="row" style="margin-top:8px">
    <button class="ghost" onclick="doPreview()">미리보기</button>
    <button class="ghost" onclick="clearTasks()">지우기</button>
  </div>
  <div class="box" id="preview" style="margin-top:10px;display:none"></div>
</section>

<section>
  <label>상품 찾기</label>
  <div class="row">
    <input id="q" placeholder="상품명 / ID 검색" oninput="renderCat()" autocapitalize="off">
    <input id="qty" type="number" value="1" min="0" style="max-width:76px;text-align:center">
  </div>
  <div class="list" id="cat"></div>
</section>

<section>
  <label>진행 상황 <span id="prgTxt" class="d"></span></label>
  <div class="prog"><i id="prg"></i></div>
  <div class="box" id="results" style="margin-top:10px">아직 실행 결과가 없습니다.</div>
  <details style="margin-top:10px"><summary>실시간 로그</summary>
    <pre class="log" id="log"></pre>
  </details>
  <details style="margin-top:6px"><summary>최근 기록 (booking_log)</summary>
    <div class="row" style="margin:8px 0">
      <select id="hr" onchange="loadHist()">
        <option>KOREA</option><option>JAPAN</option><option>AUSTRALIA</option><option>UK</option>
      </select>
      <button class="ghost" onclick="loadHist()">새로고침</button>
    </div>
    <pre class="log" id="hist"></pre>
  </details>
</section>

<div class="bar">
  <button class="primary" id="btnRun" onclick="doRun()">▶ 실행</button>
  <button class="danger" id="btnStop" onclick="doStop()" disabled>■ 중단</button>
</div>

<script>
const $ = id => document.getElementById(id);
let CAT = [], since = 0, poll = null, mode = 't';

async function api(path, body){
  const opt = body ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)} : {};
  const r = await fetch(path, opt);
  return r.json();
}

function setMode(m){
  mode = m;
  $('dTom').classList.toggle('on', m==='t');
  $('dCus').classList.toggle('on', m==='c');
  $('date').style.display = m==='c' ? 'block' : 'none';
  if(m==='c' && !$('date').value){
    const d = new Date(Date.now()+86400000);
    $('date').value = d.toISOString().slice(0,10);
  }
  dateHint();
}
$('date').addEventListener('change', dateHint);
function dateStr(){ return mode==='c' ? $('date').value : ''; }
function dateHint(){
  const d = dateStr();
  $('dateHint').textContent = d ? ('→ ' + d) : '→ 내일 (자동)';
}

/* ── 지사 상태 ── */
async function loadStatus(){
  const s = await api('/api/status');
  $('chips').innerHTML = s.regions.map(r =>
    `<button class="chip ${r.connected?'on':''}" onclick="openChrome('${r.region}')">`+
    `${r.connected?'●':'○'} ${r.display} :${r.port}</button>`).join('');
}
async function openChrome(region){
  const r = await api('/api/chrome', {region});
  alert(r.ok ? r.message : ('실패: ' + r.error));
  setTimeout(loadStatus, 4000);
}

/* ── 카탈로그 ── */
async function loadCat(){
  const c = await api('/api/catalog');
  CAT = c.items; renderCat();
}
function renderCat(){
  const kw = $('q').value.trim().toLowerCase();
  const rows = (kw ? CAT.filter(x => x.name.toLowerCase().includes(kw) || x.id.includes(kw)) : CAT).slice(0, 60);
  $('cat').innerHTML = rows.length ? rows.map(x =>
    `<div class="item"><div><div class="b">${esc(x.name)}</div>`+
    `<small>${x.region} · ${x.workflow} · ID ${x.id} · ${esc(x.accept)}</small></div>`+
    `<button class="ghost" data-name="${esc(x.name)}">추가</button></div>`).join('')
    : '<div class="item d">검색 결과 없음</div>';
}
/* 인라인 onclick 대신 이벤트 위임 — 상품명에 따옴표가 들어와도 안전 */
$('cat').addEventListener('click', e => {
  const b = e.target.closest('button[data-name]');
  if(b) addItem(b.dataset.name);
});
function addItem(name){
  const q = $('qty').value || '1';
  const cur = $('tasks').value.trim();
  $('tasks').value = (cur ? cur + ', ' : '') + name + ' ' + q;
  navigator.vibrate && navigator.vibrate(15);
}
function clearTasks(){ $('tasks').value=''; $('preview').style.display='none'; }
function esc(s){ return String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

/* ── 미리보기 ── */
async function doPreview(){
  const r = await api('/api/preview', {text:$('tasks').value, date:dateStr()});
  const p = $('preview'); p.style.display='block';
  if(!r.ok){ p.innerHTML = `<span class="rd">${esc(r.error)}</span>`; return r; }
  let h = `<div class="bl b">${esc(r.date_text)} · 총 ${r.total}건</div>`;
  r.regions.forEach(g => {
    h += `<div class="r"><div class="b">${g.display} (${g.tasks.length})</div>` +
         g.tasks.map(t => `<div class="d">· ${esc(t.name)} / ${t.workflow} / ID ${t.id} / Inv ${t.inventory}</div>`).join('') +
         `</div>`;
  });
  if(r.unknown.length) h += `<div class="r rd b">찾을 수 없음 (${r.unknown.length})</div>` +
      r.unknown.map(u => `<div class="rd">- ${esc(u.text)}: ${esc(u.memo)}</div>`).join('');
  p.innerHTML = h;
  return r;
}

/* ── 실행 ── */
async function doRun(){
  const p = await doPreview();
  if(!p || !p.ok) return;
  if(p.total === 0){ alert('실행할 작업이 없습니다.'); return; }
  const lines = p.regions.map(g => `· ${g.display} ${g.tasks.length}건`).join('\n');
  if(!confirm(`${p.date_text}\n\n${lines}\n\n총 ${p.total}건 실행할까요?`)) return;
  const r = await api('/api/run', {text:$('tasks').value, date:dateStr()});
  if(!r.ok){ alert('실행 실패: ' + r.error); return; }
  since = 0; $('log').textContent=''; $('results').innerHTML='';
  startPoll();
}
async function doStop(){
  if(!confirm('실행 중인 worker 를 종료할까요?')) return;
  const r = await api('/api/stop');
  if(!r.ok) alert(r.error);
}

/* ── 폴링 ── */
function startPoll(){ if(!poll) poll = setInterval(tick, 1200); tick(); }
async function tick(){
  const s = await api('/api/state?since=' + since);
  since = s.since;
  if(s.logs.length){
    const el = $('log');
    el.textContent += s.logs.map(l => (l.region==='SYS' ? '' : '['+l.region+'] ') + l.line).join('\n') + '\n';
    el.scrollTop = el.scrollHeight;
  }
  renderResults(s);
  $('btnRun').disabled = s.running;
  $('btnStop').disabled = !s.running;
  const done = s.results.length, tot = s.total || 0;
  $('prg').style.width = tot ? (done/tot*100)+'%' : '0';
  $('prgTxt').textContent = tot ? `${done}/${tot}` + (s.running ? ' 진행 중' : ' 완료') : '';
  if(!s.running && s.summary){ clearInterval(poll); poll=null; loadStatus(); }
}
const COLOR = {'성공':'g','실패':'rd','새버전 실패':'o','찾을 수 없음':'d'};
function renderResults(s){
  let h = '';
  if(s.summary){
    h += `<div class="r b bl">완료 · ${s.summary.duration}</div>`;
    s.summary.regions.forEach(g => {
      h += `<div class="r"><div class="b">${g.display}</div>` +
        Object.keys(g.groups).map(k => {
          const v = g.groups[k];
          return `<div class="${COLOR[k]||'d'}">${k}(${v.length}): ${v.length? esc(v.join(', ')) : '없음'}</div>`;
        }).join('') + `</div>`;
    });
  }
  if(s.results.length){
    h += s.results.slice().reverse().map(r =>
      `<div class="r"><span class="${COLOR[r.result]||'d'} b">${r.result}</span> ${esc(r.item)} `+
      `<small class="d">${r.region}</small>` + (r.memo? `<div class="d">${esc(r.memo)}</div>`:'') + `</div>`).join('');
  }
  $('results').innerHTML = h || '아직 실행 결과가 없습니다.';
}

async function loadHist(){
  const r = await api('/api/history?region=' + $('hr').value);
  $('hist').textContent = r.lines.length ? r.lines.join('\n') : '(기록 없음)';
}

dateHint(); loadStatus(); loadCat(); tick();
setInterval(loadStatus, 30000);
</script></body></html>
"""


# ──────────────────────────────────────────────────────────────────────────────
def main():
    port = DEFAULT_PORT
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass

    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print("=" * 66)
    print("  Klook Inventory Open — 모바일 리모컨 서버")
    print("=" * 66)
    print(f"  이 PC:      http://localhost:{port}/?k={TOKEN}")
    for ip in core.local_ips():
        print(f"  같은 WiFi:  http://{ip}:{port}/?k={TOKEN}")
    print()
    print(f"  토큰 파일:  {TOKEN_FILE}")
    print("  * 휴대폰에서 위 링크를 한 번 열면 쿠키가 저장되어 이후 즐겨찾기로 접속 가능")
    print("  * 외부망 접속은 Tailscale / Cloudflare Tunnel 권장 (README_MOBILE.md)")
    print("  * 종료: Ctrl+C")
    print("=" * 66)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[종료] 서버를 닫습니다.")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
