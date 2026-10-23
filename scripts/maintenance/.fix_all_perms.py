"""Add all 86+ tool permissions to approval_hook.py."""

with open("core/approval_hook.py", encoding="utf-8") as f:
    content = f.read()

# Find the start and end of _TOOL_PERMISSIONS dict
start_marker = "_TOOL_PERMISSIONS: dict[str, ToolPermissionLevel] = {"
idx_start = content.find(start_marker)
if idx_start < 0:
    print("START NOT FOUND")
    exit(1)

# Find the matching closing brace
depth = 0
idx_body = idx_start + len(start_marker)
for i in range(idx_body, len(content)):
    if content[i] == "{":
        depth += 1
    elif content[i] == "}":
        if depth == 1:
            idx_end = i + 1
            break
        depth -= 1

print(f"Replacing dict from {idx_start} to {idx_end}")
