# Agent Tools

LuckyD Code is equipped with a rich suite of built-in tools for manipulating files, executing shell commands, navigating codebases, managing git, searching the web, and driving browser automation.

---

## Core Tool Categories

### 1. File System Operations
- `view_file`: Inspect file contents with line slicing and token safety limits.
- `write_to_file`: Create new files or overwrite existing ones.
- `replace_file_content`: Targeted single-block diff replacement preserving code formatting.
- `list_dir`: Explore directory trees with recursive depth filters.
- `find_by_name`: Fast file and folder pattern searching via glob patterns.
- `grep_search`: High-performance regex and literal search powered by ripgrep.

### 2. Execution & Terminal
- `run_command`: Run bash / powershell commands with configurable timeouts, background tasks, and streaming stdout/stderr.
- `manage_task`: Check status, stream logs, or terminate background processes.

### 3. Web & Research
- `read_url_content`: Convert web pages into clean Markdown representations.
- `search_web`: Query public search engines for live documentation and technical answers.

### 4. Multi-Agent & Mesh
- `invoke_subagent`: Spawn worker agents with isolated or shared contexts.
- `send_message`: Inter-agent message passing and coordination across the agent mesh.
- `manage_subagents`: Lifecycle management for spawned concurrent tasks.

### 5. Browser Automation
- **CDP Bridge**: Execute DOM queries, take screenshots, navigate pages, and extract structured data via the LuckyD Browser Control API.
