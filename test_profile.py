import subprocess
import sys
import tempfile
import os

code = "total = sum(range(200))"

with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as code_file:
    code_file.write(code)
    code_path = code_file.name

with tempfile.NamedTemporaryFile(delete=False) as stats_file:
    stats_path = stats_file.name

safe_env = {
    "PATH": os.environ.get("PATH", ""),
    "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
}

result = subprocess.run(
    [sys.executable, "-m", "cProfile", "-o", stats_path, code_path],
    timeout=10,
    check=False,
    capture_output=True,
    env=safe_env
)
print("Return code:", result.returncode)
print("STDOUT:", result.stdout)
print("STDERR:", result.stderr)

import pstats
try:
    stats = pstats.Stats(stats_path)
    print("Stats loaded")
except Exception as e:
    print("Stats error:", e)

os.remove(code_path)
os.remove(stats_path)
