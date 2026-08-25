import os
import win32com.client
from pathlib import Path

# Create shortcut on desktop
desktop = Path.home() / 'Desktop'
shortcut_path = desktop / 'LuckyD Browser.lnk'
target = r'C:\Users\dylan\OneDrive\Desktop\coding-agent\dist\LuckyDBrowser\LuckyDBrowser.exe'
icon_path = r'C:\Users\dylan\OneDrive\Desktop\coding-agent\browser\assets\professional_icon.ico'

# Create the shortcut using WScript.Shell
shell = win32com.client.Dispatch('WScript.Shell')
shortcut = shell.CreateShortcut(str(shortcut_path))
shortcut.Targetpath = target
shortcut.WorkingDirectory = os.path.dirname(target)
shortcut.IconLocation = icon_path
shortcut.Save()

print(f"Shortcut created at: {shortcut_path}")

# Try to pin to taskbar
try:
    from pywin32.taskbarlist import TaskbarList
    taskbar = TaskbarList()
    taskbar.AddTab(str(shortcut_path))
    print("Pinned to taskbar successfully!")
except ImportError:
    print("pywin32.taskbarlist not available, shortcut created on desktop")
except Exception as e:
    print(f"Shortcut created but could not pin to taskbar: {e}")