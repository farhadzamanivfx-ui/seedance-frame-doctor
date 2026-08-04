"""
gui_app.py -- Seedance Frame Doctor desktop GUI.

A simple Tkinter front-end over core.py:
  - Pick a clip, click Analyze -> see whether it has the held/duplicate
    frame pattern and how strong it is.
  - Click Fix -> remove duplicate frames and rebuild real motion at the
    target fps (ffmpeg mpdecimate + minterpolate), instead of leaving
    held frames in place.

This file has no business logic of its own -- everything goes through
core.py so the CLI and GUI never drift apart.
"""

import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import core

APP_TITLE = "Seedance Frame Doctor"
APP_VERSION = "1.0"
APP_COPYRIGHT = "© 2026 Farhad Zamani  |  VFX Supervisor & Senior Compositor"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_TITLE}  v{APP_VERSION}")
        self.geometry("760x640")
        self.minsize(680, 580)

        self.input_path = tk.StringVar()
        self.output_path = tk.StringVar()
        self.threshold = tk.DoubleVar(value=2.0)
        self.downscale = tk.IntVar(value=320)
        self.target_fps = tk.StringVar(value="")  # blank = use source fps
        self.codec = tk.StringVar(value="prores_ks")
        self.interp_mode = tk.StringVar(value="blend")
        self.rife_model = tk.StringVar(value="")  # blank = auto-pick best
        self.rife_gpu = tk.StringVar(value="")  # blank = auto-detect

        self._build_ui()
        self._last_summary = None

    # ---------- UI construction ----------

    def _build_ui(self):
        pad = {"padx": 8, "pady": 6}

        # --- Input row ---
        frm_in = ttk.Frame(self)
        frm_in.pack(fill="x", **pad)
        ttk.Label(frm_in, text="Input clip:", width=14).pack(side="left")
        ttk.Entry(frm_in, textvariable=self.input_path).pack(
            side="left", fill="x", expand=True, padx=(0, 6)
        )
        ttk.Button(frm_in, text="Browse...", command=self._browse_input).pack(side="left")

        # --- Output row ---
        frm_out = ttk.Frame(self)
        frm_out.pack(fill="x", **pad)
        ttk.Label(frm_out, text="Output (fix):", width=14).pack(side="left")
        ttk.Entry(frm_out, textvariable=self.output_path).pack(
            side="left", fill="x", expand=True, padx=(0, 6)
        )
        ttk.Button(frm_out, text="Browse...", command=self._browse_output).pack(side="left")

        # --- Advanced settings (collapsible-ish: just a labeled frame) ---
        adv = ttk.LabelFrame(self, text="Settings")
        adv.pack(fill="x", padx=8, pady=6)

        row1 = ttk.Frame(adv)
        row1.pack(fill="x", padx=8, pady=4)
        ttk.Label(row1, text="Duplicate threshold:").pack(side="left")
        ttk.Entry(row1, textvariable=self.threshold, width=8).pack(side="left", padx=(4, 16))
        ttk.Label(row1, text="Target fps (blank = source):").pack(side="left")
        ttk.Entry(row1, textvariable=self.target_fps, width=8).pack(side="left", padx=(4, 16))
        ttk.Label(row1, text="Codec:").pack(side="left")
        ttk.Combobox(
            row1, textvariable=self.codec, width=12, state="readonly",
            values=["prores_ks", "libx264", "libx265"],
        ).pack(side="left", padx=4)

        row2 = ttk.Frame(adv)
        row2.pack(fill="x", padx=8, pady=4)
        ttk.Label(row2, text="Interpolation mode:").pack(side="left")
        ttk.Combobox(
            row2, textvariable=self.interp_mode, width=10, state="readonly",
            values=["blend", "mci", "rife", "dup"],
        ).pack(side="left", padx=4)
        ttk.Label(row2, text="RIFE model (blank=auto):").pack(side="left", padx=(16, 0))
        ttk.Entry(row2, textvariable=self.rife_model, width=12).pack(side="left", padx=4)
        ttk.Label(row2, text="GPU id (blank=auto, -1=CPU):").pack(side="left", padx=(16, 0))
        ttk.Entry(row2, textvariable=self.rife_gpu, width=6).pack(side="left", padx=4)

        row2b = ttk.Frame(adv)
        row2b.pack(fill="x", padx=8, pady=(0, 4))
        ttk.Label(
            row2b,
            text="blend = safe, no structural artifacts, slight ghost in fast motion.\n"
                 "mci = sharper, but can tear/warp at occlusion edges and fine detail\n"
                 "(hair, fur, busy backgrounds). rife = AI optical-flow interpolation\n"
                 "(needs rife-ncnn-vulkan next to this app, see README) -- handles\n"
                 "occlusion and fine detail far better than blend/mci.",
            foreground="#555", justify="left",
        ).pack(side="left")

        row2c = ttk.Frame(adv)
        row2c.pack(fill="x", padx=8, pady=(0, 4))
        ttk.Label(
            row2c,
            text="Fix removes exactly the frames flagged above by Analyze "
                 "(same threshold), then retimes the rest.",
            foreground="#555",
        ).pack(side="left")

        # --- Action buttons ---
        frm_actions = ttk.Frame(self)
        frm_actions.pack(fill="x", padx=8, pady=4)
        self.btn_analyze = ttk.Button(frm_actions, text="Analyze", command=self._on_analyze)
        self.btn_analyze.pack(side="left", padx=(0, 8))
        self.btn_fix = ttk.Button(frm_actions, text="Fix", command=self._on_fix)
        self.btn_fix.pack(side="left")

        self.progress = ttk.Progressbar(frm_actions, mode="indeterminate")
        self.progress.pack(side="left", fill="x", expand=True, padx=(16, 0))

        # --- Log output ---
        frm_log = ttk.Frame(self)
        frm_log.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.log = tk.Text(frm_log, wrap="word", height=20)
        self.log.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(frm_log, command=self.log.yview)
        scroll.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=scroll.set)

        self.status = tk.StringVar(value="Ready.")
        ttk.Label(self, textvariable=self.status, anchor="w").pack(fill="x", padx=8, pady=(0, 2))

        # --- Copyright bar ---
        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=0, pady=0)
        ttk.Label(
            self, text=APP_COPYRIGHT,
            anchor="center", foreground="#888", font=("", 8),
        ).pack(fill="x", pady=(2, 4))

    # ---------- helpers ----------

    def _browse_input(self):
        path = filedialog.askopenfilename(
            title="Choose source clip",
            filetypes=[("Video files", "*.mp4 *.mov *.mxf *.avi *.mkv"), ("All files", "*.*")],
        )
        if path:
            self.input_path.set(path)
            if not self.output_path.get():
                base, ext = os.path.splitext(path)
                self.output_path.set(f"{base}_fixed.mov")

    def _browse_output(self):
        path = filedialog.asksaveasfilename(
            title="Save fixed clip as",
            defaultextension=".mov",
            filetypes=[("QuickTime / MOV", "*.mov"), ("MP4", "*.mp4"), ("All files", "*.*")],
        )
        if path:
            self.output_path.set(path)

    def _log_write(self, text):
        self.log.insert("end", text + "\n")
        self.log.see("end")

    def _set_busy(self, busy, status_text=""):
        self.btn_analyze.configure(state="disabled" if busy else "normal")
        self.btn_fix.configure(state="disabled" if busy else "normal")
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()
        if status_text:
            self.status.set(status_text)

    def _run_in_thread(self, target):
        t = threading.Thread(target=target, daemon=True)
        t.start()

    # ---------- actions ----------

    def _on_analyze(self):
        path = self.input_path.get().strip()
        if not path:
            messagebox.showwarning(APP_TITLE, "Choose an input clip first.")
            return

        self.log.delete("1.0", "end")
        self._log_write(f"Analyzing: {path}")
        self._set_busy(True, "Analyzing...")

        def work():
            try:
                results, fps = core.analyze_duplicates(
                    path, diff_threshold=self.threshold.get(),
                    downscale_width=self.downscale.get(),
                )
                summary = core.summarize(results, fps)
                self._last_summary = summary
                report_text = core.format_report(summary)
                self.after(0, lambda: self._log_write("\n" + report_text))
                self.after(0, lambda: self._set_busy(False, "Analysis complete."))
            except core.ToolError as e:
                self.after(0, lambda: self._log_write(f"\nError: {e}"))
                self.after(0, lambda: self._set_busy(False, "Analysis failed."))
            except Exception as e:
                self.after(0, lambda: self._log_write(f"\nUnexpected error: {e}"))
                self.after(0, lambda: self._set_busy(False, "Analysis failed."))

        self._run_in_thread(work)

    def _on_fix(self):
        in_path = self.input_path.get().strip()
        out_path = self.output_path.get().strip()
        if not in_path or not out_path:
            messagebox.showwarning(APP_TITLE, "Choose both an input clip and an output path.")
            return

        fps_text = self.target_fps.get().strip()
        fps_value = float(fps_text) if fps_text else None

        self._log_write(f"\nFixing: {in_path}\n  -> {out_path}")
        self._set_busy(True, "Running ffmpeg (dedupe + retime)...")

        def on_line(line):
            self.after(0, lambda: self._log_write(line))

        def work():
            try:
                info, target_fps, summary = core.run_fix(
                    in_path, out_path,
                    threshold=self.threshold.get(), downscale_width=self.downscale.get(),
                    fps=fps_value, codec=self.codec.get(),
                    interp_mode=self.interp_mode.get(),
                    model=(self.rife_model.get().strip() or None),
                    gpu_id=(int(self.rife_gpu.get()) if self.rife_gpu.get().strip() else None),
                    on_line=on_line,
                )
                self.after(0, lambda: self._log_write(
                    f"\nDone. Removed {summary['dup_count']} of {summary['total']} "
                    f"frames, retimed to {target_fps:.3f} fps.\nOutput: {out_path}"
                ))
                self.after(0, lambda: self._set_busy(False, "Fix complete."))
            except core.ToolError as e:
                self.after(0, lambda: self._log_write(f"\nError: {e}"))
                self.after(0, lambda: self._set_busy(False, "Fix failed."))
            except Exception as e:
                self.after(0, lambda: self._log_write(f"\nUnexpected error: {e}"))
                self.after(0, lambda: self._set_busy(False, "Fix failed."))

        self._run_in_thread(work)


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
