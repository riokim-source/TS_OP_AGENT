# -*- coding: utf-8 -*-
"""
gui.py
Klook Merchant Center 익일 Inventory Open — 데스크톱 GUI 버전.

main.py(CLI) 와 실행방식 동일:
  Chrome(CDP 9522~9525) → region 별 klook_worker.py 병렬 실행 → ##RESULT## 수집
  → logs/booking_log_KLOOK_*.txt 기록
차이는 입력을 stdin 대신 창에서 받고, 진행 상황을 실시간으로 보여준다는 것뿐.

실행:
  python gui.py          (또는 start_gui.bat 더블클릭)
"""
from __future__ import annotations

import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

import klook_core as core

FONT      = ("Malgun Gothic", 10)
FONT_BOLD = ("Malgun Gothic", 10, "bold")
FONT_MONO = ("Consolas", 9)
FONT_H1   = ("Malgun Gothic", 13, "bold")

RESULT_COLOR = {
    "성공":        "#1a7f37",
    "실패":        "#cf222e",
    "새버전 실패": "#bc4c00",
    "찾을 수 없음": "#6e7781",
}


class KlookGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Klook Inventory Open — 라스트미닛 봇")
        self.geometry("1280x820")
        self.minsize(1080, 700)

        self.q: queue.Queue = queue.Queue()
        self.runner: core.Runner | None = None
        self.preview_cache: dict | None = None
        self.status_labels: dict[str, ttk.Label] = {}
        self.catalog = core.product_catalog()

        self.status_var = tk.StringVar(
            value=f"준비됨 · 매핑 {len(self.catalog)}개 · 로그 {core.LOG_DIR}")

        self._build_style()
        self._build_header()
        self._build_statusbar()
        self._build_body()

        self.after(80, self._pump)
        self.refresh_cdp()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── 스타일 ────────────────────────────────────────────────────────────
    def _build_style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("vista")
        except tk.TclError:
            pass
        st.configure(".", font=FONT)
        st.configure("H1.TLabel", font=FONT_H1)
        st.configure("Run.TButton", font=FONT_BOLD)
        st.configure("Treeview", font=FONT, rowheight=24)
        st.configure("Treeview.Heading", font=FONT_BOLD)

    # ── 헤더: Chrome / CDP 상태 ───────────────────────────────────────────
    def _build_header(self):
        bar = ttk.Frame(self, padding=(12, 10, 12, 6))
        bar.pack(fill="x")

        ttk.Label(bar, text="Klook Inventory Open", style="H1.TLabel").pack(side="left")
        ttk.Label(bar, text="  ·  packages.py 매핑 자동 분류").pack(side="left")

        ttk.Button(bar, text="상태 새로고침", command=self.refresh_cdp).pack(side="right")

        chips = ttk.Frame(self, padding=(12, 0, 12, 8))
        chips.pack(fill="x")
        for region in core.SUPPORTED_REGIONS:
            box = ttk.Frame(chips, relief="groove", padding=(8, 4))
            box.pack(side="left", padx=(0, 8))
            lbl = ttk.Label(box, text=f"● {core.REGION_DISPLAY[region]} :{core.cdp_port(region)} 확인중",
                            foreground="#6e7781")
            lbl.pack(side="left", padx=(0, 6))
            ttk.Button(box, text="Chrome 열기", width=11,
                       command=lambda r=region: self.launch_chrome(r)).pack(side="left")
            self.status_labels[region] = lbl

        ttk.Separator(self).pack(fill="x")

    # ── 본문 ──────────────────────────────────────────────────────────────
    def _build_body(self):
        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=12, pady=8)

        left = ttk.Frame(paned)
        right = ttk.Frame(paned)
        paned.add(left, weight=2)
        paned.add(right, weight=3)

        self._build_input(left)
        self._build_tabs(right)

    def _build_input(self, parent):
        # 날짜
        datef = ttk.LabelFrame(parent, text="1. 대상 날짜", padding=10)
        datef.pack(fill="x")
        self.date_mode = tk.StringVar(value="tomorrow")
        ttk.Radiobutton(datef, text="내일 (기본)", value="tomorrow", variable=self.date_mode,
                        command=self._on_date_mode).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(datef, text="특정 날짜", value="custom", variable=self.date_mode,
                        command=self._on_date_mode).grid(row=0, column=1, sticky="w", padx=(16, 6))
        self.date_entry = ttk.Entry(datef, width=16, state="disabled")
        self.date_entry.grid(row=0, column=2, sticky="w")
        ttk.Label(datef, text="MM/DD 또는 YYYY-MM-DD", foreground="#6e7781").grid(
            row=0, column=3, sticky="w", padx=(8, 0))
        self.date_hint = ttk.Label(datef, text="", foreground="#0969da")
        self.date_hint.grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))
        self._update_date_hint()

        # 작업 입력
        inf = ttk.LabelFrame(parent, text="2. 작업 목록  (상품명 수량, 상품명 수량)", padding=10)
        inf.pack(fill="both", expand=True, pady=(10, 0))

        self.input_text = tk.Text(inf, height=12, font=FONT, wrap="word", undo=True)
        vs = ttk.Scrollbar(inf, orient="vertical", command=self.input_text.yview)
        self.input_text.configure(yscrollcommand=vs.set)
        self.input_text.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.input_text.insert("1.0", "")
        self.input_text.bind("<KeyRelease>", lambda e: self._invalidate_preview())

        hint = ttk.Label(parent, foreground="#6e7781", justify="left",
                         text="예시)  남쁘 5, Kumamoto Takachiho(한) 4, Toyako Niseko 2\n"
                              "수량 0 → Inventory 0 + Activate OFF (마감)")
        hint.pack(fill="x", pady=(6, 0))

        # 버튼
        btns = ttk.Frame(parent)
        btns.pack(fill="x", pady=(10, 0))
        self.btn_preview = ttk.Button(btns, text="미리보기 (분류 확인)", command=self.do_preview)
        self.btn_preview.pack(side="left")
        self.btn_run = ttk.Button(btns, text="▶ 실행", style="Run.TButton", command=self.do_run)
        self.btn_run.pack(side="left", padx=(8, 0))
        self.btn_stop = ttk.Button(btns, text="■ 중단", command=self.do_stop, state="disabled")
        self.btn_stop.pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="입력 지우기",
                   command=lambda: (self.input_text.delete("1.0", "end"), self._invalidate_preview())
                   ).pack(side="right")

        # 미리보기 결과
        prev = ttk.LabelFrame(parent, text="3. 분류 미리보기", padding=8)
        prev.pack(fill="both", expand=True, pady=(10, 0))
        self.preview_text = tk.Text(prev, height=9, font=FONT_MONO, wrap="word",
                                    state="disabled", background="#f6f8fa")
        pv = ttk.Scrollbar(prev, orient="vertical", command=self.preview_text.yview)
        self.preview_text.configure(yscrollcommand=pv.set)
        self.preview_text.pack(side="left", fill="both", expand=True)
        pv.pack(side="right", fill="y")
        self.preview_text.tag_configure("warn", foreground="#cf222e")
        self.preview_text.tag_configure("head", font=("Consolas", 9, "bold"))

    def _build_tabs(self, parent):
        self.nb = ttk.Notebook(parent)
        self.nb.pack(fill="both", expand=True)

        # ── 상품 찾기 ──
        tab_cat = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab_cat, text="상품 찾기")

        top = ttk.Frame(tab_cat)
        top.pack(fill="x")
        ttk.Label(top, text="검색").pack(side="left")
        self.search_var = tk.StringVar()
        ent = ttk.Entry(top, textvariable=self.search_var)
        ent.pack(side="left", fill="x", expand=True, padx=6)
        ent.bind("<KeyRelease>", lambda e: self._fill_catalog())
        self.region_filter = tk.StringVar(value="ALL")
        cmb = ttk.Combobox(top, textvariable=self.region_filter, width=11, state="readonly",
                           values=["ALL"] + list(core.SUPPORTED_REGIONS))
        cmb.pack(side="left")
        cmb.bind("<<ComboboxSelected>>", lambda e: self._fill_catalog())
        ttk.Label(top, text=" 수량").pack(side="left", padx=(8, 2))
        self.qty_var = tk.StringVar(value="1")
        ttk.Spinbox(top, from_=0, to=999, width=5, textvariable=self.qty_var).pack(side="left")
        ttk.Button(top, text="추가 →", command=self._add_selected).pack(side="left", padx=(6, 0))

        cols = ("name", "region", "workflow", "id", "accept")
        self.tree = ttk.Treeview(tab_cat, columns=cols, show="headings", selectmode="extended")
        for c, t, w in (("name", "상품명", 240), ("region", "지사", 80),
                        ("workflow", "방식", 80), ("id", "ID", 80), ("accept", "마감", 90)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        ts = ttk.Scrollbar(tab_cat, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ts.set)
        self.tree.pack(side="left", fill="both", expand=True, pady=(8, 0))
        ts.pack(side="right", fill="y", pady=(8, 0))
        self.tree.bind("<Double-1>", lambda e: self._add_selected())
        self._fill_catalog()

        # ── 진행 로그 ──
        tab_log = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab_log, text="진행 로그")
        self.log_text = tk.Text(tab_log, font=FONT_MONO, wrap="none",
                                background="#0d1117", foreground="#c9d1d9",
                                insertbackground="#c9d1d9", state="disabled")
        ls = ttk.Scrollbar(tab_log, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=ls.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        ls.pack(side="right", fill="y")
        for tag, color in (("err", "#ff7b72"), ("warn", "#e3b341"), ("step", "#79c0ff"),
                           ("ok", "#7ee787"), ("sys", "#d2a8ff"), ("dim", "#8b949e")):
            self.log_text.tag_configure(tag, foreground=color)

        # ── 결과 ──
        tab_res = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab_res, text="결과")
        rcols = ("item", "region", "result", "memo")
        self.res_tree = ttk.Treeview(tab_res, columns=rcols, show="headings")
        for c, t, w in (("item", "상품 / 수량", 240), ("region", "지사", 80),
                        ("result", "결과", 100), ("memo", "메모", 380)):
            self.res_tree.heading(c, text=t)
            self.res_tree.column(c, width=w, anchor="w")
        rs = ttk.Scrollbar(tab_res, orient="vertical", command=self.res_tree.yview)
        self.res_tree.configure(yscrollcommand=rs.set)
        self.res_tree.pack(side="left", fill="both", expand=True)
        rs.pack(side="right", fill="y")
        for label, color in RESULT_COLOR.items():
            self.res_tree.tag_configure(label, foreground=color)

        # ── 최근 기록 ──
        tab_hist = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab_hist, text="최근 기록")
        htop = ttk.Frame(tab_hist)
        htop.pack(fill="x")
        self.hist_region = tk.StringVar(value="KOREA")
        hcmb = ttk.Combobox(htop, textvariable=self.hist_region, width=12, state="readonly",
                            values=list(core.SUPPORTED_REGIONS))
        hcmb.pack(side="left")
        hcmb.bind("<<ComboboxSelected>>", lambda e: self._load_history())
        ttk.Button(htop, text="새로고침", command=self._load_history).pack(side="left", padx=6)
        ttk.Button(htop, text="logs 폴더 열기",
                   command=lambda: subprocess.Popen(["explorer", str(core.LOG_DIR)])).pack(side="left")
        self.hist_text = tk.Text(tab_hist, font=FONT_MONO, wrap="none", state="disabled",
                                 background="#f6f8fa")
        hs = ttk.Scrollbar(tab_hist, orient="vertical", command=self.hist_text.yview)
        self.hist_text.configure(yscrollcommand=hs.set)
        self.hist_text.pack(side="left", fill="both", expand=True, pady=(8, 0))
        hs.pack(side="right", fill="y", pady=(8, 0))
        self._load_history()

    def _build_statusbar(self):
        ttk.Separator(self).pack(side="bottom", fill="x")
        bar = ttk.Frame(self, padding=(12, 4))
        bar.pack(side="bottom", fill="x")
        ttk.Label(bar, textvariable=self.status_var, foreground="#6e7781").pack(side="left")
        self.prog = ttk.Progressbar(bar, mode="determinate", length=220)
        self.prog.pack(side="right")

    # ── 상품 카탈로그 ─────────────────────────────────────────────────────
    def _fill_catalog(self):
        kw = self.search_var.get().strip().casefold()
        region = self.region_filter.get()
        self.tree.delete(*self.tree.get_children())
        n = 0
        for row in self.catalog:
            if region != "ALL" and row["region"] != region:
                continue
            if kw and kw not in row["name"].casefold() and kw not in row["id"]:
                continue
            self.tree.insert("", "end", values=(row["name"], core.REGION_DISPLAY[row["region"]],
                                                row["workflow"], row["id"], row["accept"]))
            n += 1
        self.status_var.set(f"상품 {n}개 표시 · 전체 매핑 {len(self.catalog)}개")

    def _add_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        qty = self.qty_var.get().strip() or "1"
        if not qty.isdigit():
            messagebox.showwarning("수량 오류", "수량은 숫자여야 합니다.")
            return
        current = self.input_text.get("1.0", "end").strip()
        parts = [f"{self.tree.item(i, 'values')[0]} {qty}" for i in sel]
        joined = ", ".join(parts)
        self.input_text.delete("1.0", "end")
        self.input_text.insert("1.0", f"{current}, {joined}" if current else joined)
        self._invalidate_preview()

    # ── 날짜 ──────────────────────────────────────────────────────────────
    def _on_date_mode(self):
        if self.date_mode.get() == "custom":
            self.date_entry.configure(state="normal")
            self.date_entry.focus_set()
        else:
            self.date_entry.configure(state="disabled")
        self._update_date_hint()

    def _update_date_hint(self):
        try:
            self.date_hint.configure(text="→ " + core.describe_date(self._target_date()),
                                     foreground="#0969da")
        except ValueError as e:
            self.date_hint.configure(text=f"→ {e}", foreground="#cf222e")

    def _target_date(self) -> str | None:
        if self.date_mode.get() == "tomorrow":
            return None
        return core.normalize_date(self.date_entry.get())

    # ── 미리보기 ──────────────────────────────────────────────────────────
    def _invalidate_preview(self):
        self.preview_cache = None

    def do_preview(self) -> dict | None:
        self._update_date_hint()
        text = self.input_text.get("1.0", "end").strip()
        if not text:
            messagebox.showinfo("입력 없음", "작업 목록을 입력하세요.")
            return None
        try:
            target_date = self._target_date()
        except ValueError as e:
            messagebox.showerror("날짜 오류", str(e))
            return None

        parsed = core.parse_input(text)
        parsed["target_date"] = target_date
        self.preview_cache = parsed

        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", "end")
        self.preview_text.insert("end", f"대상 날짜: {core.describe_date(target_date)}\n\n", "head")
        for region in core.SUPPORTED_REGIONS:
            tasks = parsed["region_tasks"].get(region, [])
            if not tasks:
                continue
            self.preview_text.insert("end", f"[{core.REGION_DISPLAY[region]}] {len(tasks)}개\n", "head")
            for t in tasks:
                self.preview_text.insert(
                    "end", f"  · {t['name']} / {t['workflow']} / ID {t['package_id']} / Inv {t['inventory']}\n")
        if parsed["unknown"]:
            self.preview_text.insert("end", "\n[실행 안 함 — 찾을 수 없음]\n", "warn")
            for u in parsed["unknown"]:
                self.preview_text.insert("end", f"  - {u.get('input_text')}: {u.get('memo')}\n", "warn")
        if parsed["total"] == 0 and not parsed["unknown"]:
            self.preview_text.insert("end", "실행할 작업이 없습니다.\n", "warn")
        self.preview_text.configure(state="disabled")

        self.status_var.set(f"미리보기: 실행 {parsed['total']}건 / 매핑실패 {len(parsed['unknown'])}건")
        return parsed

    # ── 실행 ──────────────────────────────────────────────────────────────
    def do_run(self):
        if self.runner and self.runner.running:
            messagebox.showwarning("실행 중", "이미 실행 중입니다.")
            return
        parsed = self.preview_cache or self.do_preview()
        if not parsed:
            return
        if parsed["total"] == 0:
            messagebox.showinfo("작업 없음", "실행할 작업이 없습니다. (매핑된 상품 없음)")
            return

        active = [r for r in core.SUPPORTED_REGIONS if parsed["region_tasks"].get(r)]
        statuses = core.cdp_status_all()
        self._render_cdp(statuses)
        down = [core.REGION_DISPLAY[r] for r in active if not statuses[r]["ok"]]
        if down:
            if not messagebox.askyesno(
                    "Chrome 미연결",
                    f"연결되지 않은 지사 Chrome: {', '.join(down)}\n\n"
                    "해당 지사 작업은 실패로 기록됩니다. 그래도 실행할까요?"):
                return

        lines = [f"· {core.REGION_DISPLAY[r]} {len(parsed['region_tasks'][r])}건" for r in active]
        if not messagebox.askokcancel(
                "실행 확인",
                f"대상 날짜: {core.describe_date(parsed['target_date'])}\n\n"
                + "\n".join(lines)
                + f"\n\n총 {parsed['total']}건을 실행합니다."):
            return

        self.res_tree.delete(*self.res_tree.get_children())
        self._clear_log()
        self.nb.select(1)
        self.prog.configure(maximum=parsed["total"], value=0)
        self.done_count = 0
        self.total_count = parsed["total"]

        self.runner = core.Runner(
            parsed["region_tasks"], parsed["unknown"], parsed["target_date"],
            on_log=lambda r, l: self.q.put(("log", r, l)),
            on_result=lambda r, res: self.q.put(("result", r, res)),
            on_done=lambda s: self.q.put(("done", None, s)),
        )
        self.btn_run.configure(state="disabled")
        self.btn_preview.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.status_var.set("실행 중…")
        try:
            self.runner.start()
        except Exception as e:
            self._finish_ui()
            messagebox.showerror("실행 실패", str(e))

    def do_stop(self):
        if self.runner and self.runner.running:
            if messagebox.askyesno("중단", "실행 중인 worker 를 종료할까요?\n(진행 중이던 항목은 실패로 기록됩니다)"):
                self.runner.stop()

    def _finish_ui(self):
        self.btn_run.configure(state="normal")
        self.btn_preview.configure(state="normal")
        self.btn_stop.configure(state="disabled")

    # ── 큐 펌프 (worker 스레드 → Tk) ──────────────────────────────────────
    def _pump(self):
        try:
            while True:
                kind, region, payload = self.q.get_nowait()
                if kind == "log":
                    self._append_log(region, payload)
                elif kind == "result":
                    self._append_result(region, payload)
                elif kind == "done":
                    self._on_done(payload)
        except queue.Empty:
            pass
        self.after(80, self._pump)

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _append_log(self, region, line):
        tag = "dim"
        if "[오류]" in line or "[치명적" in line:
            tag = "err"
        elif "[주의]" in line:
            tag = "warn"
        elif "[단계]" in line:
            tag = "step"
        elif "[완료]" in line:
            tag = "ok"
        if region == "*":
            tag = "sys"
            prefix = ""
        else:
            prefix = f"[{core.REGION_DISPLAY.get(region, region)}] "
        self.log_text.configure(state="normal")
        self.log_text.insert("end", prefix + line + "\n", tag)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _append_result(self, region, res):
        result = str(res.get("result", "")).strip()
        if str(res.get("workflow", "")).lower() == "activity" and result == "실패":
            result = "새버전 실패"
        memo = str(res.get("memo", "")).strip()
        self.res_tree.insert("", "end", tags=(result,), values=(
            core.cli.item_text_of(res), core.REGION_DISPLAY.get(region, region), result, memo[:300]))
        self.res_tree.see(self.res_tree.get_children()[-1])
        self.done_count = getattr(self, "done_count", 0) + 1
        self.prog.configure(value=self.done_count)
        self.status_var.set(f"실행 중… {self.done_count}/{getattr(self, 'total_count', '?')}")

    def _on_done(self, summary):
        self._finish_ui()
        self.nb.select(2)
        parts = []
        for region, data in summary["regions"].items():
            g = data["groups"]
            parts.append(f"[{data['display']}] " + " / ".join(
                f"{label} {len(g[label])}" for label in core.RESULT_LABELS if g[label]))
        self.status_var.set(
            f"완료 · {summary['duration_text']} · " + ("  ".join(parts) if parts else "결과 없음"))

        msg_lines = [f"총 런닝타임: {summary['duration_text']}", ""]
        for region, data in summary["regions"].items():
            g = data["groups"]
            msg_lines.append(f"[{data['display']}]")
            for label in core.RESULT_LABELS:
                items = g[label]
                msg_lines.append(f"  {label}({len(items)}): {', '.join(items) if items else '없음'}")
            msg_lines.append("")
        msg_lines.append(f"로그: {summary['log_dir']}")
        self._load_history()
        if summary["stopped"]:
            messagebox.showwarning("중단됨", "\n".join(msg_lines))
        elif summary["failed"]:
            messagebox.showwarning("완료 (실패 포함)", "\n".join(msg_lines))
        else:
            messagebox.showinfo("완료", "\n".join(msg_lines))

    # ── CDP 상태 ──────────────────────────────────────────────────────────
    def refresh_cdp(self):
        def work():
            statuses = core.cdp_status_all()
            self.after(0, lambda: self._render_cdp(statuses))
        threading.Thread(target=work, daemon=True).start()

    def _render_cdp(self, statuses):
        for region, st in statuses.items():
            lbl = self.status_labels.get(region)
            if not lbl:
                continue
            if st["ok"]:
                lbl.configure(text=f"● {core.REGION_DISPLAY[region]} :{st['port']} 연결됨",
                              foreground="#1a7f37")
            else:
                lbl.configure(text=f"○ {core.REGION_DISPLAY[region]} :{st['port']} 미연결",
                              foreground="#8b949e")

    def launch_chrome(self, region):
        try:
            msg = core.launch_chrome(region)
            self.status_var.set(msg)
            self.after(4000, self.refresh_cdp)
        except Exception as e:
            messagebox.showerror("Chrome 실행 실패", str(e))

    # ── 최근 기록 ─────────────────────────────────────────────────────────
    def _load_history(self):
        lines = core.tail_log(self.hist_region.get(), 200)
        self.hist_text.configure(state="normal")
        self.hist_text.delete("1.0", "end")
        self.hist_text.insert("end", "\n".join(lines) if lines else "(기록 없음)")
        self.hist_text.see("end")
        self.hist_text.configure(state="disabled")

    # ── 종료 ──────────────────────────────────────────────────────────────
    def _on_close(self):
        if self.runner and self.runner.running:
            if not messagebox.askyesno("종료", "실행 중입니다. 종료하면 worker 가 중단됩니다. 종료할까요?"):
                return
            self.runner.stop()
        self.destroy()


if __name__ == "__main__":
    KlookGUI().mainloop()
