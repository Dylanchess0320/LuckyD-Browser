# -*- mode: python ; coding: utf-8 -*-
#
# Build the interactive LuckyD Code CLI exe:
#   python -m PyInstaller --noconfirm --clean main.spec
#
# Produces dist\luckyd-cli.exe -- the REAL interactive REPL (main.py + ui.py):
# rich-rendered streaming, /help, /tools, /model, /sessions, etc., and every
# tool the agent has registered -- including the multi-agent "mesh" tools
# (AgentHandoff, TeamCreate, SendMessage, ReceiveMessage, ListAgents from
# tools/agent_orchestration.py, plus SubAgent from tools/subagent_tool.py).
#
# THIS IS DIFFERENT FROM luckyd-code.exe (built from web_server.py, via
# luckyd-code.spec): that one is a headless HTTP server for the browser's HQ
# iframe -- it has no stdin loop, no prompt, nothing to type into. Launching
# it in an interactive terminal just prints a server banner and hangs.
# luckyd-cli.exe is what browser/browser_core/terminal_server.py's "agent"
# shell actually spawns for the Terminal tab.

from PyInstaller.utils.hooks import collect_all

# pydantic v2 is imported at module level by features/deep_research (schemas,
# models, workers) which the frozen interactive CLI pulls in via
# main.py -> agent -> features. It must be INSTALLED in the
# build env (see browser/requirements.txt); when it isn't, the frozen app
# dies at launch with "ModuleNotFoundError: No module named 'pydantic...".
# Even when installed, pydantic v2 resolves most of its own submodules lazily
# (PEP 562 __getattr__ + import_module in pydantic/__init__.py: BaseModel,
# Field, _internal, ...), which is invisible to modulegraph -- so collect the
# whole package explicitly. Same for pydantic_core: its compiled Rust
# extension (_pydantic_core.pyd) must be bundled or every BaseModel
# definition fails. Without these the browser dies on startup.
_pd_datas, _pd_binaries, _pd_hidden = [], [], []
for _pkg in ('pydantic', 'pydantic_core'):
    try:
        _d, _b, _h = collect_all(_pkg)
        _pd_datas += _d
        _pd_binaries += _b
        _pd_hidden += _h
    except Exception:
        pass

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=_pd_binaries,
    datas=[
        ('core', 'core'),
        ('llm', 'llm'),
        ('memory', 'memory'),
        ('tools', 'tools'),
        # 9.7: Markdown slash-command templates (/compact /review /init
        # /skills) live at repo root; bundle them so the frozen CLI discovers
        # its built-in commands.
        ('slash_commands', 'slash_commands'),
        ('features', 'features'),
        ('project', 'project'),
        ('skills', 'skills'),
        ('agent.py', '.'),
        ('config.py', '.'),
        ('model_resolver.py', '.'),
        ('ui.py', '.'),
        # Bundle the Cline session reader so the frozen CLI can use ClinePass
        # (reads ~/.cline/data/settings/providers.json) with no API key.
        ('browser/browser_core/cline_session.py', '.'),
        # Never embed a developer's real .env/API keys in a distributable CLI.
        ('.env.example', '.env.example'),
    ] + _pd_datas,
    hiddenimports=[
        'httpx', 'httpcore', 'h11', 'certifi', 'idna', 'sniffio', 'anyio',
        'cline_session',
        # Deep Research swarm (tools/deep_research_tool.py + features/deep_research):
        # langgraph orchestration, Gemini grounding, keyless DDG search.
        'langgraph', 'langgraph.graph', 'google.genai', 'ddgs',
        # Rich powers the real terminal experience here (unlike
        # luckyd-code.spec, which excludes it -- that exe never renders a
        # prompt so it doesn't need it. This one does.)
        'rich', 'rich.console', 'rich.live', 'rich.markdown', 'rich.spinner',
        'rich.table', 'rich.text', 'rich.theme', 'rich.status', 'rich.prompt',
        'rich._spinners',
    ] + _pd_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Same modulegraph-crash workarounds as luckyd-code.spec (py3.10.0 dis
    # bug on certain optional-import packages) -- see that file for details.
    excludes=[
        'torch', 'torchaudio', 'torchvision', 'onnxruntime', 'transformers',
        'numpy', 'scipy', 'sklearn', 'pandas', 'matplotlib', 'sentence_transformers',
        'playwright', 'websockets', 'browser_use', 'selenium',
        'PIL', 'mss', 'pyautogui', 'pygetwindow', 'pyperclip', 'win10toast',
        'yaml', 'jedi', 'requests', 'aiohttp',
        'openai', 'anthropic', 'cryptography',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='luckyd-cli',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
