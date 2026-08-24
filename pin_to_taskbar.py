from pathlib import Path

from pywin32.taskbarlist import TaskbarList

# Pin the shortcut to taskbar
desktop = Path.home() / "Desktop"
shortcut_path = desktop / "LuckyD Browser.lnk"

try:
    taskbar = TaskbarList()
    taskbar.AddTab(str(shortcut_path))
    print("✅ Successfully pinned to taskbar!")
except ImportError:
    print("pywin32.taskbarlist module not available")
except Exception as e:
    print(f"Could not pin to taskbar: {e}")
    print("   The shortcut is still on your desktop at:", shortcut_path)
