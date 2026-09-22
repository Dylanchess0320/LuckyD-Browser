import asyncio
import time
from pathlib import Path
import tools.lsp_tools


class MockPath:
    def __init__(self, path):
        self.path = path
        self.name = Path(path).name

    def read_text(self, *args, **kwargs):
        time.sleep(0.1)  # Simulate slow disk I/O
        return ""

    def __str__(self):
        return str(self.path)

    def resolve(self):
        return self

    def exists(self):
        return True


original_resolve_path = tools.lsp_tools._resolve_path
tools.lsp_tools._resolve_path = lambda path: MockPath(path)


class MockJedi:
    class Script:
        def __init__(self, code, path):
            pass

        def help(self, line, column):
            time.sleep(0.01)  # Simulate jedi processing

            class Result:
                name = "test"
                type = "function"

                def docstring(self):
                    return "doc"

            return [Result()]


tools.lsp_tools._get_jedi = lambda: MockJedi


# PATCH LspHoverTool
class OptimizedHoverTool(tools.lsp_tools.ToolBase):
    name = "LspHover"

    async def execute(self, file_path: str, line: int, character: int = 0):
        try:
            jedi = tools.lsp_tools._get_jedi()
            path = tools.lsp_tools._resolve_path(file_path)

            def _get_hover():
                source = path.read_text()
                script = jedi.Script(code=source, path=str(path))
                return script.help(line=line, column=character)

            results = await asyncio.to_thread(_get_hover)

            if not results:
                return tools.lsp_tools.ToolOutput(text="No type info found.", title="Hover")

            parts = []
            for r in results:
                parts.append(f"Name: {r.name}")
                parts.append(f"Type: {r.type}")
                if r.docstring():
                    parts.append(f"\n{r.docstring()[:500]}")

            return tools.lsp_tools.ToolOutput(
                text="\n".join(parts),
                title=f"Type Info: {results[0].name}",
                metadata={"name": results[0].name, "type": results[0].type},
            )
        except Exception as e:
            return tools.lsp_tools.ToolOutput(text=f"LSP error: {e}", error=True)


class Monitor:
    def __init__(self):
        self.blocked_times = []
        self.running = True

    async def loop(self):
        while self.running:
            start = time.perf_counter()
            await asyncio.sleep(0.01)
            elapsed = time.perf_counter() - start
            if elapsed > 0.02:
                self.blocked_times.append(elapsed)


async def run_workload():
    tool = OptimizedHoverTool()
    tasks = [tool.execute("fake_file.py", 1, 0) for _ in range(10)]
    await asyncio.gather(*tasks)


async def main():
    print("Starting benchmark for optimized LspHoverTool (asyncio.to_thread)...")
    monitor = Monitor()
    monitor_task = asyncio.create_task(monitor.loop())

    start_time = time.perf_counter()
    await run_workload()
    end_time = time.perf_counter()

    monitor.running = False
    await monitor_task

    blocked_times = monitor.blocked_times

    print(f"Total workload time: {end_time - start_time:.3f}s")
    if blocked_times:
        print(f"Total number of loop blocks > 20ms: {len(blocked_times)}")
        print(f"Max block time: {max(blocked_times):.3f}s")
        print(f"Total blocked time: {sum(blocked_times):.3f}s")
    else:
        print("No loop blocks > 20ms detected.")


if __name__ == "__main__":
    asyncio.run(main())
