"""OTA Close Bot - Tkinter GUI (자동 연동 + GLOBAL 포함 + 봇별 병렬 실행)."""
from __future__ import annotations
import io, os, queue, sys, threading, time
import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import scrolledtext, ttk

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

AGENCIES = ["klook", "kkday", "gg", "vi", "mrt"]
CHROMES  = ["KOREA", "JAPAN", "AUSTRALIA", "UK", "GLOBAL"]

AGENCY_TO_CHROMES = {
    "klook": ["KOREA", "JAPAN", "AUSTRALIA", "UK"],
    "kkday": ["KOREA", "JAPAN", "AUSTRALIA", "UK"],
    "gg":    ["KOREA", "JAPAN", "AUSTRALIA", "UK"],
    "vi":    ["GLOBAL"],
    "mrt":   ["GLOBAL"],
}


class StdoutRedirector(io.IOBase):
    def __init__(self, q):
        super().__init__()
        self.q = q
    def write(self, s):
        if s:
            self.q.put(s)
        return len(s)
    def flush(self): pass
    def writable(self): return True


class OTACloseGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("OTA Close Bot")
        self.root.geometry("820x680")
        self.log_queue = queue.Queue()
        self.bot_thread = None
        self._syncing = False
        self._build()
        self._wire_auto_link()
        self._pump()

    def _build(self):
        df = ttk.LabelFrame(self.root, text="Date")
        df.pack(fill="x", padx=8, pady=4)
        tm = (datetime.now().date() + timedelta(days=1)).strftime("%Y-%m-%d")
        self.date_var = tk.StringVar(value=tm)
        ttk.Entry(df, textvariable=self.date_var, width=14, font=("Consolas", 10)).pack(side="left", padx=6, pady=4)
        for txt, d in [("Today", 0), ("Tomorrow", 1), ("+2d", 2)]:
            ttk.Button(df, text=txt, width=9,
                       command=lambda dd=d: self.date_var.set((datetime.now().date()+timedelta(days=dd)).strftime("%Y-%m-%d"))
                       ).pack(side="left", padx=2)
        ttk.Label(df, text="(YYYY-MM-DD)", foreground="gray").pack(side="left", padx=8)

        af = ttk.LabelFrame(self.root, text="Agencies")
        af.pack(fill="x", padx=8, pady=4)
        self.agency_vars = {}
        for ag in AGENCIES:
            v = tk.BooleanVar(value=False)  # 초기 OFF - 사용자가 직접 선택
            self.agency_vars[ag] = v
            ttk.Checkbutton(af, text=ag.upper(), variable=v).pack(side="left", padx=6, pady=4)
        ttk.Button(af, text="All ON", command=self._all_agencies_on).pack(side="left", padx=4)
        ttk.Button(af, text="All OFF", command=self._all_agencies_off).pack(side="left", padx=2)

        rf = ttk.LabelFrame(self.root, text="Chrome (auto-linked from Agency)")
        rf.pack(fill="x", padx=8, pady=4)
        self.chrome_vars = {}
        for c in CHROMES:
            v = tk.BooleanVar(value=False)  # 초기 OFF - 에이전시 선택 시 자동 연동
            self.chrome_vars[c] = v
            ttk.Checkbutton(rf, text=c, variable=v).pack(side="left", padx=6, pady=4)
        ttk.Button(rf, text="All", command=lambda:[v.set(True) for v in self.chrome_vars.values()]).pack(side="left", padx=4)
        ttk.Button(rf, text="Clear", command=lambda:[v.set(False) for v in self.chrome_vars.values()]).pack(side="left", padx=2)

        # Dry-run UI 제거 - 항상 실제 실행 (False 고정).
        # 백엔드 호환 위해 변수 자체는 유지.
        self.dry_run_var = tk.BooleanVar(value=False)

        bf = ttk.Frame(self.root)
        bf.pack(fill="x", padx=8, pady=6)
        self.run_btn = ttk.Button(bf, text=">>  RUN", width=12, command=self.start_run)
        self.run_btn.pack(side="left", padx=4)
        ttk.Button(bf, text="Clear log", command=self._clear_all_logs).pack(side="left", padx=4)
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(bf, textvariable=self.status_var, foreground="darkblue").pack(side="left", padx=12)

        # Notebook: ALL LOG / KKDAY / KLOOK / GG / VI / MRT / RESULT
        lf = ttk.LabelFrame(self.root, text="Logs")
        lf.pack(fill="both", expand=True, padx=8, pady=4)
        self.notebook = ttk.Notebook(lf)
        self.notebook.pack(fill="both", expand=True, padx=4, pady=4)

        self.tabs = {}        # key -> ScrolledText
        self.tab_frames = {}  # key -> Frame (for .select())
        TAB_ORDER = [
            ("ALL", "ALL LOG"),
            ("KLOOK", "KLOOK"),
            ("KKDAY", "KKDAY"),
            ("GG", "GG"),
            ("VI", "VI"),
            ("MRT", "MRT"),
            ("RESULT", "RESULT"),
        ]
        for key, label in TAB_ORDER:
            frame = ttk.Frame(self.notebook)
            self.notebook.add(frame, text=label)
            text = scrolledtext.ScrolledText(frame, font=("Consolas", 9), wrap="word", height=22)
            text.pack(fill="both", expand=True, padx=2, pady=2)
            self.tabs[key] = text
            self.tab_frames[key] = frame

        # 호환용 alias (기존 self.log_text 참조 대비)
        self.log_text = self.tabs["ALL"]

        # 라인 단위 분배용 buffer
        self._line_buffer = ""

    def _wire_auto_link(self):
        for ag, var in self.agency_vars.items():
            var.trace_add("write", lambda *a: self._recompute_chromes())

    def _recompute_chromes(self):
        if self._syncing:
            return
        needed = set()
        for ag, var in self.agency_vars.items():
            if var.get():
                for c in AGENCY_TO_CHROMES.get(ag, []):
                    needed.add(c)
        self._syncing = True
        try:
            for c, cv in self.chrome_vars.items():
                cv.set(c in needed)
        finally:
            self._syncing = False

    def _all_agencies_on(self):
        for v in self.agency_vars.values():
            v.set(True)

    def _all_agencies_off(self):
        for v in self.agency_vars.values():
            v.set(False)

    def _pump(self):
        """큐에서 텍스트 꺼내 라인 단위로 분배."""
        try:
            while True:
                chunk = self.log_queue.get_nowait()
                self._line_buffer += chunk
                # 완성된 라인들만 분배 (마지막 \n 까지)
                while "\n" in self._line_buffer:
                    idx = self._line_buffer.index("\n")
                    line = self._line_buffer[: idx + 1]
                    self._line_buffer = self._line_buffer[idx + 1 :]
                    self._distribute_line(line)
        except queue.Empty:
            pass
        self.root.after(50, self._pump)

    def _classify_line(self, line):
        """라인 prefix 보고 어느 봇 탭으로 분배할지 결정. None = ALL only."""
        # [KKDAY], [KKDAY/...], [KLOOK/...], [GG/...], [VI], [MRT/...] 등 매칭
        for bot in ("KKDAY", "KLOOK", "GG", "VI", "MRT"):
            if f"[{bot}]" in line or f"[{bot}/" in line:
                return bot
        return None

    def _distribute_line(self, line):
        """ALL LOG 에는 항상, 매칭되는 봇 탭에 추가로."""
        # ALL
        self.tabs["ALL"].insert(tk.END, line)
        self.tabs["ALL"].see(tk.END)
        # 봇별
        bot = self._classify_line(line)
        if bot and bot in self.tabs:
            self.tabs[bot].insert(tk.END, line)
            self.tabs[bot].see(tk.END)

    def _log(self, s):
        self.log_queue.put(s)

    def _clear_all_logs(self):
        for t in self.tabs.values():
            t.delete("1.0", tk.END)
        self._line_buffer = ""

    def start_run(self):
        if self.bot_thread and self.bot_thread.is_alive():
            self._log("[GUI] Already running\n")
            return
        date_s = self.date_var.get().strip()
        try:
            target = datetime.strptime(date_s, "%Y-%m-%d").date()
        except ValueError:
            self._log(f"[GUI] Date format error: '{date_s}'\n")
            return
        agencies = [a for a, v in self.agency_vars.items() if v.get()]
        if not agencies:
            self._log("[GUI] Select at least 1 agency\n")
            return
        chromes = [c for c, v in self.chrome_vars.items() if v.get()]
        dry_run = self.dry_run_var.get()  # 항상 False
        self.run_btn.config(state="disabled")
        self.status_var.set(f"Running... date={date_s}")
        self._log("\n" + "=" * 70 + "\n")
        self._log(f"[GUI] Start: date={date_s} agencies={agencies} chromes={chromes}\n")
        self._log("=" * 70 + "\n\n")
        self.bot_thread = threading.Thread(
            target=self._run_bots,
            args=(target, agencies, chromes, dry_run),
            daemon=True,
        )
        self.bot_thread.start()

    def _run_bots(self, target, agencies, chromes, dry_run):
        regions_for_agencies = [c for c in chromes if c != "GLOBAL"]
        os.environ["KKDAY_REGIONS"] = ",".join(regions_for_agencies)
        os.environ["KLOOK_REGIONS"] = ",".join(regions_for_agencies)
        os.environ["GG_REGIONS"] = ",".join(regions_for_agencies)

        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = StdoutRedirector(self.log_queue)
        sys.stderr = StdoutRedirector(self.log_queue)
        results = []
        try:
            try:
                from shared.health import REGION_CHROME_MAP, ensure_chrome, is_port_alive
                self._log(f"[GUI] Chrome auto-boot for: {chromes}\n")
                for region in chromes:
                    if region not in REGION_CHROME_MAP:
                        continue
                    port, bat = REGION_CHROME_MAP[region]
                    if is_port_alive(port):
                        self._log(f"  Chrome[{region}] port {port} : already OK\n")
                    else:
                        self._log(f"  Chrome[{region}] port {port} : booting via {bat}\n")
                        ensure_chrome(port, bat, wait_sec=8)
                time.sleep(2)
                for region in chromes:
                    if region not in REGION_CHROME_MAP:
                        continue
                    port, _ = REGION_CHROME_MAP[region]
                    sign = "OK" if is_port_alive(port) else "DEAD"
                    self._log(f"  Chrome[{region}] port {port} : {sign}\n")
                self._log("\n")
            except Exception as e:
                self._log(f"[GUI] Chrome boot error (continue): {e}\n")

            target_str = target.strftime("%Y-%m-%d")
            try:
                import main as ota_main
                try:
                    # GUI 가 이미 필요한 Chrome 만 부팅했으므로 boot=False
                    results = ota_main.run_once(
                        dry_run=dry_run, agencies=agencies,
                        target_date=target_str, boot=False,
                    )
                except TypeError:
                    try:
                        results = ota_main.run_once(
                            dry_run=dry_run, agencies=agencies, target_date=target_str
                        )
                    except TypeError:
                        results = ota_main.run_once(dry_run=dry_run, agencies=agencies)
            except Exception as e:
                self._log(f"[GUI] main.run_once 호출 실패: {e}\n")

            self._log("\n" + "=" * 70 + "\n[GUI] Total Summary\n" + "=" * 70 + "\n")
            ts = tf = tk_ = 0
            for r in results or []:
                self._log(f"  {r['agency']:<8} ok={r['success']:>4} fail={r['failed']:>4} skip={r['skipped']:>4}\n")
                ts += r["success"]; tf += r["failed"]; tk_ += r["skipped"]
                for err in r.get("errors", [])[:5]:
                    self._log(f"      L {err}\n")
            self._log("-" * 70 + "\n")
            self._log(f"  TOTAL    ok={ts:>4} fail={tf:>4} skip={tk_:>4}\n")
            self._log("=" * 70 + "\n")
        except Exception as e:
            self._log(f"\n[GUI] Exception: {e}\n")
        finally:
            sys.stdout = old_out
            sys.stderr = old_err
            self.root.after(0, self._done, results)

    def _done(self, results):
        self.run_btn.config(state="normal")
        # 분배 buffer 에 남은 미완성 라인도 RESULT 직전에 flush
        if self._line_buffer:
            self._distribute_line(self._line_buffer)
            self._line_buffer = ""

        if results:
            ok = sum(r["success"] for r in results)
            fail = sum(r["failed"] for r in results)
            skip = sum(r["skipped"] for r in results)
            # RESULT 탭 컨텐츠 생성
            result_text = self.tabs["RESULT"]
            result_text.delete("1.0", tk.END)
            result_text.insert(tk.END, "=" * 70 + "\n")
            result_text.insert(tk.END, f"OTA Close Bot Summary - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            result_text.insert(tk.END, "=" * 70 + "\n\n")
            for r in results:
                result_text.insert(tk.END,
                    f"  {r['agency']:<8} ok={r['success']:>4} fail={r['failed']:>4} skip={r['skipped']:>4}\n")
                for err in r.get("errors", [])[:10]:
                    result_text.insert(tk.END, f"      L {err}\n")
            result_text.insert(tk.END, "-" * 70 + "\n")
            result_text.insert(tk.END, f"  TOTAL    ok={ok:>4} fail={fail:>4} skip={skip:>4}\n")
            result_text.insert(tk.END, "=" * 70 + "\n")
            # 자동으로 RESULT 탭 전환
            try:
                self.notebook.select(self.tab_frames["RESULT"])
            except Exception:
                pass
            self.status_var.set(f"Done - ok={ok} fail={fail} skip={skip}")
        else:
            self.status_var.set("Done")


def main():
    root = tk.Tk()
    OTACloseGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
