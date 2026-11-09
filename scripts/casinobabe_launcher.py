#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Casinobabe Desktop Launcher — START / PAUSE / STOP for all components.
Components:
  - Fishing Bot (scripts/fishing_bot.py)
  - Fishing Observer (scripts/fishing_observer.py)
  - Bridge (bridge/bridge.mjs)

Features:
  - Single-window GUI with component status indicators
  - START / PAUSE / STOP per component
  - Log panel with real-time output
  - Single-instance protection (no double starts)
  - Clean shutdown on close
  - No console window (when run with pythonw)
  - Desktop shortcut creation
"""

import os
import sys
import subprocess
import threading
import time
import json
import signal
import atexit
from pathlib import Path
from datetime import datetime

try:
    import tkinter as tk
    from tkinter import ttk, scrolledtext, messagebox
except ImportError:
    print("tkinter not available. Install python3-tk or use system python.")
    sys.exit(1)

ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = ROOT / "scripts"
BRIDGE_DIR = ROOT / "bridge"
STATE_DIR = ROOT / "state"
REPORTS_DIR = ROOT / "reports"
CAPTURES_DIR = ROOT / "captures"

# Ensure directories exist
for d in [STATE_DIR, REPORTS_DIR, CAPTURES_DIR]:
    d.mkdir(parents=True, exist_ok=True)

PID_FILE = STATE_DIR / "casinobabe_launcher.pid"
LOG_FILE = REPORTS_DIR / "launcher.log"

class ProcessManager:
    """Manages a single subprocess with proper lifecycle."""
    
    def __init__(self, name, cmd, cwd=None, env=None):
        self.name = name
        self.cmd = cmd
        self.cwd = cwd or ROOT
        self.env = env or os.environ.copy()
        self.proc = None
        self.thread = None
        self.running = False
        self.paused = False
        self.log_callback = None
        
    def set_log_callback(self, cb):
        self.log_callback = cb
        
    def _log(self, msg):
        if self.log_callback:
            self.log_callback(f"[{self.name}] {msg}")
        
    def start(self):
        if self.running:
            self._log("Already running")
            return False
            
        try:
            self.proc = subprocess.Popen(
                self.cmd,
                cwd=self.cwd,
                env=self.env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            )
            self.running = True
            self.paused = False
            self.thread = threading.Thread(target=self._read_output, daemon=True)
            self.thread.start()
            self._log("STARTED")
            return True
        except Exception as e:
            self._log(f"FAILED TO START: {e}")
            return False
            
    def _read_output(self):
        while self.running and self.proc and self.proc.stdout:
            try:
                line = self.proc.stdout.readline()
                if not line:
                    break
                if self.log_callback:
                    self.log_callback(f"[{self.name}] {line.rstrip()}")
            except Exception:
                break
        self.running = False
        
    def stop(self):
        if not self.running:
            self._log("Not running")
            return
            
        self.running = False
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
            except Exception:
                pass
        self._log("STOPPED")
        
    def pause(self):
        # For components that support pause (like fishing_bot)
        self.paused = not self.paused
        self._log("PAUSED" if self.paused else "RESUMED")
        
    def status(self):
        return "RUNNING" if self.running else ("PAUSED" if self.paused else "STOPPED")

class LauncherApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Casinobabe Launcher")
        self.root.geometry("900x600")
        self.root.minsize(800, 500)
        
        # Single instance check
        if PID_FILE.exists():
            try:
                old_pid = int(PID_FILE.read_text().strip())
                import psutil
                if psutil.pid_exists(old_pid):
                    messagebox.showerror("Already Running", 
                        f"Launcher already running (PID {old_pid}).\nClose the other instance first.")
                    root.destroy()
                    return
            except Exception:
                pass
        PID_FILE.write_text(str(os.getpid()))
        atexit.register(self._cleanup)
        
        self.managers = {}
        self._setup_components()
        self._build_ui()
        self._update_status_loop()
        
    def _setup_components(self):
        python = sys.executable
        
        # Fishing Bot
        self.managers["fishing_bot"] = ProcessManager(
            "Fishing Bot",
            [python, str(SCRIPTS_DIR / "fishing_bot.py"), "--cli"],
            cwd=ROOT
        )
        
        # Fishing Observer (loop mode, 60s interval)
        self.managers["fishing_observer"] = ProcessManager(
            "Fishing Observer",
            [python, str(SCRIPTS_DIR / "fishing_observer.py"), "--loop", "60"],
            cwd=ROOT
        )
        
        # Bridge
        self.managers["bridge"] = ProcessManager(
            "Bridge",
            ["node", str(BRIDGE_DIR / "bridge.mjs")],
            cwd=BRIDGE_DIR
        )
        
        for mgr in self.managers.values():
            mgr.set_log_callback(self._append_log)
            
    def _build_ui(self):
        # Main layout
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Component controls
        controls_frame = ttk.LabelFrame(main_frame, text="Components", padding=10)
        controls_frame.pack(fill=tk.X, pady=(0, 10))
        
        self.status_labels = {}
        self.start_buttons = {}
        self.pause_buttons = {}
        self.stop_buttons = {}
        
        for i, (name, mgr) in enumerate(self.managers.items()):
            row = ttk.Frame(controls_frame)
            row.pack(fill=tk.X, pady=4)
            
            ttk.Label(row, text=name, width=20).pack(side=tk.LEFT, padx=5)
            
            status = ttk.Label(row, text="STOPPED", width=12, 
                              foreground="red", font=("Consolas", 9, "bold"))
            status.pack(side=tk.LEFT, padx=5)
            self.status_labels[name] = status
            
            btn_start = ttk.Button(row, text="START", width=10,
                                  command=lambda n=name: self._start_component(n))
            btn_start.pack(side=tk.LEFT, padx=3)
            self.start_buttons[name] = btn_start
            
            btn_pause = ttk.Button(row, text="PAUSE", width=10,
                                  command=lambda n=name: self._pause_component(n))
            btn_pause.pack(side=tk.LEFT, padx=3)
            self.pause_buttons[name] = btn_pause
            
            btn_stop = ttk.Button(row, text="STOP", width=10,
                                 command=lambda n=name: self._stop_component(n))
            btn_stop.pack(side=tk.LEFT, padx=3)
            self.stop_buttons[name] = btn_stop
            
        # Global controls
        global_frame = ttk.Frame(controls_frame)
        global_frame.pack(fill=tk.X, pady=(10, 0))
        
        ttk.Button(global_frame, text="START ALL", width=15,
                  command=self._start_all).pack(side=tk.LEFT, padx=5)
        ttk.Button(global_frame, text="STOP ALL", width=15,
                  command=self._stop_all).pack(side=tk.LEFT, padx=5)
        ttk.Button(global_frame, text="Create Shortcut", width=15,
                  command=self._create_shortcut).pack(side=tk.LEFT, padx=5)
        
        # Log panel
        log_frame = ttk.LabelFrame(main_frame, text="Logs", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True)
        
        self.log_text = scrolledtext.ScrolledText(
            log_frame, wrap=tk.WORD, font=("Consolas", 9), 
            state=tk.DISABLED, height=20
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)
        
        log_btn_frame = ttk.Frame(log_frame)
        log_btn_frame.pack(fill=tk.X, pady=(5, 0))
        ttk.Button(log_btn_frame, text="Clear Log", command=self._clear_log).pack(side=tk.LEFT)
        ttk.Button(log_btn_frame, text="Save Log", command=self._save_log).pack(side=tk.LEFT, padx=5)
        
        # Initial log
        self._append_log("Casinobabe Launcher started")
        self._append_log(f"Root: {ROOT}")
        self._append_log(f"WoW process: {'WowClassic.exe' if self._wow_running() else 'NOT RUNNING'}")
        
    def _wow_running(self):
        try:
            import psutil
            for proc in psutil.process_iter(['name']):
                if 'wow' in proc.info['name'].lower() or 'WowClassic' in proc.info['name']:
                    return True
        except Exception:
            pass
        return False
        
    def _append_log(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {msg}\n"
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, line)
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)
        
        # Also write to file
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass
            
    def _clear_log(self):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)
        
    def _save_log(self):
        try:
            content = self.log_text.get(1.0, tk.END)
            path = REPORTS_DIR / f"launcher_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            path.write_text(content, encoding="utf-8")
            self._append_log(f"Log saved to {path}")
        except Exception as e:
            self._append_log(f"Failed to save log: {e}")
            
    def _start_component(self, name):
        mgr = self.managers.get(name)
        if mgr and mgr.start():
            self._update_button_states(name)
            
    def _pause_component(self, name):
        mgr = self.managers.get(name)
        if mgr:
            mgr.pause()
            self._update_button_states(name)
            
    def _stop_component(self, name):
        mgr = self.managers.get(name)
        if mgr:
            mgr.stop()
            self._update_button_states(name)
            
    def _start_all(self):
        for name in self.managers:
            self._start_component(name)
            
    def _stop_all(self):
        for name in self.managers:
            self._stop_component(name)
            
    def _update_button_states(self, name):
        mgr = self.managers.get(name)
        if not mgr:
            return
            
        status = mgr.status()
        label = self.status_labels.get(name)
        if label:
            color = "green" if status == "RUNNING" else ("orange" if status == "PAUSED" else "red")
            label.config(text=status, foreground=color)
            
        btn_start = self.start_buttons.get(name)
        btn_pause = self.pause_buttons.get(name)
        btn_stop = self.stop_buttons.get(name)
        
        if status == "RUNNING":
            if btn_start: btn_start.config(state=tk.DISABLED)
            if btn_pause: btn_pause.config(state=tk.NORMAL)
            if btn_stop: btn_stop.config(state=tk.NORMAL)
        elif status == "PAUSED":
            if btn_start: btn_start.config(state=tk.NORMAL)
            if btn_pause: btn_pause.config(state=tk.NORMAL)
            if btn_stop: btn_stop.config(state=tk.NORMAL)
        else:  # STOPPED
            if btn_start: btn_start.config(state=tk.NORMAL)
            if btn_pause: btn_pause.config(state=tk.DISABLED)
            if btn_stop: btn_stop.config(state=tk.DISABLED)
            
    def _update_status_loop(self):
        for name, mgr in self.managers.items():
            # Check if process died
            if mgr.running and mgr.proc and mgr.proc.poll() is not None:
                mgr.running = False
                self._append_log(f"[{name}] Process exited with code {mgr.proc.returncode}")
                
        for name in self.managers:
            self._update_button_states(name)
            
        self.root.after(1000, self._update_status_loop)
        
    def _create_shortcut(self):
        try:
            import winshell
            from win32com.client import Dispatch
            
            desktop = winshell.desktop()
            shortcut_path = os.path.join(desktop, "Casinobabe Launcher.lnk")
            
            shell = Dispatch('WScript.Shell')
            shortcut = shell.CreateShortCut(shortcut_path)
            shortcut.Targetpath = sys.executable
            shortcut.Arguments = f'"{__file__}"'
            shortcut.WorkingDirectory = str(ROOT)
            shortcut.IconLocation = sys.executable
            shortcut.save()
            
            self._append_log(f"Desktop shortcut created: {shortcut_path}")
            messagebox.showinfo("Success", "Desktop shortcut created!")
        except ImportError:
            self._append_log("winshell/pywin32 not installed, cannot create shortcut")
            messagebox.showwarning("Missing Dependency", 
                "Install pywin32 and winshell to create shortcuts:\npip install pywin32 winshell")
        except Exception as e:
            self._append_log(f"Failed to create shortcut: {e}")
            messagebox.showerror("Error", f"Failed to create shortcut: {e}")
            
    def _cleanup(self):
        self._stop_all()
        try:
            PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass
            
    def _stop_all(self):
        for name in self.managers:
            self._stop_component(name)
            
    def on_close(self):
        self._cleanup()
        self.root.destroy()

def main():
    # Check if running as pythonw (no console)
    if hasattr(sys, 'frozen') or 'pythonw' in sys.executable.lower():
        pass  # No console
    
    root = tk.Tk()
    app = LauncherApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()

if __name__ == "__main__":
    main()