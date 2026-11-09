#!/usr/bin/env python3
"""Create desktop shortcut for Casinobabe Fishing Assistant"""
import os
import win32com.client

# Paths
script_dir = r"C:\factory\worktrees\casinobabe\casinobabe-1791392099272-msxoms\fishing_assistant_app"
bat_path = os.path.join(script_dir, "casinobabe-fishing-assistant.bat")
shortcut_name = "Casinobabe-Fishing-Assistant.lnk"
desktop = os.path.join(os.path.expanduser("~"), "Desktop")
shortcut_path = os.path.join(desktop, shortcut_name)

# Remove existing shortcut if present
if os.path.exists(shortcut_path):
    os.remove(shortcut_path)

# Create the shortcut
wsh = win32com.client.Dispatch("WScript.Shell")
lnk = wsh.CreateShortcut(shortcut_path)
lnk.TargetPath = bat_path
lnk.WorkingDirectory = script_dir
lnk.Save()

print("Shortcut created at:", shortcut_path)