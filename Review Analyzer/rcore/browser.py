from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from selenium import webdriver
from selenium.webdriver.chrome.options import Options


@dataclass
class BrowserSession:
    debug_address: str = "127.0.0.1:9222"
    driver: Any = None
    preload_id: str | None = None

    def connect(self) -> None:
        options = Options()
        options.add_experimental_option("debuggerAddress", self.debug_address)
        self.driver = webdriver.Chrome(options=options)
        self.driver.set_script_timeout(90)

    def close(self) -> None:
        # Do not quit Chrome: this is an attached debugging session.
        self.driver = None

    def _clear_preload(self) -> None:
        if self.preload_id is None or self.driver is None:
            return
        try:
            self.driver.execute_cdp_cmd(
                "Page.removeScriptToEvaluateOnNewDocument",
                {"identifier": self.preload_id},
            )
        except Exception:
            pass
        self.preload_id = None

    def capture_request(
        self,
        page_url: str,
        endpoint: str,
        body_contains: str = "",
        wait: int = 10,
    ) -> dict | None:
        """Capture the first matching fetch/XHR made by the logged-in review page."""
        if self.driver is None:
            raise RuntimeError("Chrome is not connected")

        self._clear_preload()
        hook = """
        (function(){
          window.__CAP__ = null;
          var WANT = %s;
          var NEED = %s;
          function save(url, method, headers, body){
            try{
              if(String(url).indexOf(WANT) >= 0
                 && (!NEED || (body && String(body).indexOf(NEED) >= 0))
                 && !window.__CAP__){
                window.__CAP__ = {url:String(url), method:(method||'GET'), headers:(headers||{}), body:(body||null)};
              }
            }catch(e){}
          }
          // 헤더는 객체 · Headers · [[k,v],...] 배열 · 그 밖의 iterable 로 올 수 있다.
          // 예전에는 배열을 그대로 for..in 으로 돌려서 {0:.., 1:..} 같은 쓸모없는
          // 헤더가 만들어졌다. 그러면 authorization 이 빠져 재생 요청이 401 이 된다.
          // (GetYourGuide 가 실제로 이 경로였고, 수집이 조용히 0건이 됐다.)
          function toHeaders(hs){
            var h = {};
            if(!hs) return h;
            try{
              if(typeof Headers !== 'undefined' && hs instanceof Headers){
                hs.forEach(function(v,k){ h[k]=v; });
                return h;
              }
              if(Array.isArray(hs)){
                for(var i=0;i<hs.length;i++){
                  var p=hs[i];
                  if(p && p.length>=2) h[String(p[0])]=String(p[1]);
                }
                return h;
              }
              if(typeof Symbol!=='undefined' && hs[Symbol.iterator]){
                var it=hs[Symbol.iterator]();
                var r=it.next();
                while(!r.done){
                  var q=r.value;
                  if(q && q.length>=2) h[String(q[0])]=String(q[1]);
                  r=it.next();
                }
                return h;
              }
              for(var k in hs){
                var v=hs[k];
                if(typeof v==='string' || typeof v==='number') h[k]=String(v);
              }
            }catch(e){}
            return h;
          }
          function mergeHeaders(){
            var out={};
            for(var i=0;i<arguments.length;i++){
              var h=toHeaders(arguments[i]);
              for(var k in h){ if(out[k]===undefined) out[k]=h[k]; }
            }
            return out;
          }
          var of = window.fetch;
          window.fetch = function(){
            var a = arguments;
            try{
              var url = (a[0] && a[0].url) || a[0];
              var m = (a[1] && a[1].method) || (a[0] && a[0].method) || 'GET';
              // init 의 헤더가 우선. Request 객체로 온 헤더도 함께 모은다.
              var h = mergeHeaders(a[1] && a[1].headers, a[0] && a[0].headers);
              var b = (a[1] && a[1].body) || null;
              if(a[0] instanceof Request && !b){
                a[0].clone().text().then(function(t){ save(url,m,h,t); });
              } else { save(url,m,h,b); }
            }catch(e){}
            return of.apply(this, a);
          };
          var O = XMLHttpRequest.prototype.open,
              S = XMLHttpRequest.prototype.send,
              SR = XMLHttpRequest.prototype.setRequestHeader;
          XMLHttpRequest.prototype.open = function(m,u){ this.__m=m; this.__u=u; this.__h={}; return O.apply(this, arguments); };
          XMLHttpRequest.prototype.setRequestHeader = function(k,v){ this.__h[k]=v; return SR.apply(this, arguments); };
          XMLHttpRequest.prototype.send = function(b){ save(this.__u, this.__m, this.__h, b); return S.apply(this, arguments); };
        })();
        """ % (json.dumps(endpoint), json.dumps(body_contains or ""))

        res = self.driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument", {"source": hook}
        )
        self.preload_id = res.get("identifier")

        self.driver.get(page_url)
        deadline = time.time() + wait
        while time.time() < deadline:
            cap = self.driver.execute_script("return window.__CAP__ || null;")
            if cap:
                return cap
            time.sleep(0.4)
        return None

    def fetch_page(self, js: str, *args: Any) -> dict:
        if self.driver is None:
            raise RuntimeError("Chrome is not connected")
        wrapper = (
            "var done = arguments[arguments.length-1];\n"
            "var P = Array.prototype.slice.call(arguments, 0, arguments.length-1);\n"
            "(async function(){ try{\n"
            + js
            + "\n}catch(e){ done({error:String(e)}); } })();"
        )
        return self.driver.execute_async_script(wrapper, *args)
