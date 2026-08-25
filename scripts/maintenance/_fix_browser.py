import re

with open("tools/browser_tools.py", newline="") as f:
    content = f.read()

# Replace nested if with combined condition
old = r'if not name\.startswith\("_"\):\r?\n\s+if dlow in name\.lower\(\):'
new = r'if not name.startswith("_") and dlow in name.lower():'
content = re.sub(old, new, content)

with open("tools/browser_tools.py", "w", newline="") as f:
    f.write(content)
print("OK")
