import winshell
from win32com.client import Dispatch
import os

desktop = winshell.desktop()
shortcut_path = os.path.join(desktop, 'Casinobabe Launcher.lnk')

shell = Dispatch('WScript.Shell')
shortcut = shell.CreateShortCut(shortcut_path)
shortcut.Targetpath = r'C:\Users\user\AppData\Local\Programs\Python\Python311\pythonw.exe'
shortcut.Arguments = r'"C:\factory\worktrees\casinobabe\casinobabe-1791392099272-msxoms\scripts\casinobabe_launcher.py"'
shortcut.WorkingDirectory = r'C:\factory\worktrees\casinobabe\casinobabe-1791392099272-msxoms'
shortcut.IconLocation = r'C:\Users\user\AppData\Local\Programs\Python\Python311\pythonw.exe'
shortcut.save()

print(f'Shortcut created: {shortcut_path}')