import asyncio
import time
from tools.file_tools import GrepTool


async def background_task():
    start = time.time()
    ticks = 0
    while time.time() - start < 1.0:
        await asyncio.sleep(0.05)
        ticks += 1
    print(f"Background task ticked {ticks} times")
    return ticks


async def run_grep(tool):
    print("Starting grep...")
    start = time.time()
    # Search for something common across many files, maybe multiple times to amplify
    for _ in range(5):
        await tool.execute("import", path=".", output_mode="count")
    end = time.time()
    print(f"Grep took: {end - start:.4f} seconds")


async def main():
    tool = GrepTool()

    start_total = time.time()

    # Run both background task and grep concurrently
    task1 = asyncio.create_task(background_task())
    task2 = asyncio.create_task(run_grep(tool))

    await asyncio.gather(task1, task2)

    end_total = time.time()
    print(f"Total time: {end_total - start_total:.4f} seconds")


if __name__ == "__main__":
    asyncio.run(main())
