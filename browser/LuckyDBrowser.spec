# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all, collect_submodules

# pywinpty powers the in-browser terminal (browser_core.terminal_server does
# a lazy "from winpty import PTY"), so modulegraph never sees it — collect the
# whole package explicitly: _winpty.pyd plus the ConPTY native sidekicks
# (conpty.dll, OpenConsole.exe, winpty.dll, winpty-agent.exe) that must sit
# beside it in _internal/winpty/. Without these the /terminal tab's WS->PTY
# bridge dies on every connection with "terminal failed to start".
wp_datas, wp_binaries, wp_hiddenimports = collect_all('winpty')
ws_hiddenimports = collect_submodules('websockets')

# The Deep Research swarm imports langgraph lazily; its transitive chain
# (langchain_core -> uuid_utils Rust .pyd, langsmith, xxhash, ...) must be
# bundled wholesale or the frozen swarm dies with "module
# 'langchain_core.runnables'.'base'' not found (No module named
# 'uuid_utils._uuid_utils')" and llm_calls=0. (The swarm also has a
# dependency-free sequential fallback, but the graph path needs this.)
# Fetch/search deps (trafilatura/bs4/lxml/tldextract/tenacity/ddgs) are also
# collected: without them frozen deep-reads yield zero text-backed evidence.
_swarm_datas, _swarm_binaries, _swarm_hidden = [], [], []
for _pkg in (
    'langchain_core',
    'langgraph',
    'langgraph_checkpoint',
    'langgraph_prebuilt',
    'langgraph_sdk',
    'langsmith',
    'uuid_utils',
    'xxhash',
    'httpx_sse',
    'orjson',
    'jsonpatch',
    'requests_toolbelt',
    'ddgs',
    'duckduckgo_search',
    'trafilatura',
    'bs4',
    'beautifulsoup4',
    'lxml',
    'tldextract',
    'tenacity',
    'google.genai',
):
    try:
        _d, _b, _h = collect_all(_pkg)
        _swarm_datas += _d
        _swarm_binaries += _b
        _swarm_hidden += _h
    except Exception:
        pass

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=wp_binaries + _swarm_binaries,
    # Bundle the coding-agent backend beside LuckyDBrowser.exe so the frozen
    # app can auto-start the harness anywhere (portable or installed) —
    # browser_core.harness_bridge._find_exe() checks the exe's own folder.
    # The LIVE-source harness (luckyd-harness.py + web_server.py) is preferred
    # over the frozen luckyd-code.exe because it runs the FIXED llm_client.
    # We bundle the lean backend source (core/llm/memory/tools + entry modules)
    # so the HQ works on machines without the full repo; heavy deps (torch,
    # playwright, …) are optional and degrade gracefully.
    datas=[('assets', 'assets'),
           ('../luckyd-harness.py', '.'),
           ('../web_server.py', '.'),
           ('../agent.py', '.'),
           ('../config.py', '.'),
           ('../model_resolver.py', '.'),
           ('../ui.py', '.'),
           ('../project', 'project'),
           ('../core', 'core'),
           ('../llm', 'llm'),
           ('../memory', 'memory'),
           ('../tools', 'tools'),
           ('../features', 'features'),
           ('../skills', 'skills'),
           # Ship browser_core (esp. cline_session.py) as a data folder so the
           # bundled core/providers.py can locate it at browser/browser_core/
           # relative to _internal (its __file__.parent.parent) in the frozen app.
           ('browser_core', 'browser/browser_core'),
           ('../luckyd-code.exe', '.'),
           # The real interactive terminal CLI (main.py via main.spec) --
           # distinct from luckyd-code.exe above (the headless HQ server).
           # browser_core/terminal_server.py's "agent" shell spawns this one.
           ('../luckyd-cli.exe', '.'),
           # Ship the clean template (never the dev .env — it holds real keys),
           # plus a ready-made _internal/.env so the bundled harness/terminal
           # exes default to free local Ollama on end-user machines.
           ('../.env.example', '.env.example'),
           ('installer/env/.env', '.'),
           # Bundled Deck Studio (Marp pipeline UI + decks + themes). The
            # TileRegistry autostarts it from %APPDIR%\studio; ai.js falls back
            # to the app-injected GOOGLE_API_KEY, so no secret ships in here.
            ('studio', 'studio')] + wp_datas + _swarm_datas,
    # websockets + winpty are imported lazily (CDP driver / screenshots /
    # terminal bridge) - pin them. assets/ ships recursively, including
    # assets/terminal/ (the vendored xterm.js page the /terminal tab needs).
    hiddenimports=ws_hiddenimports + wp_hiddenimports + _swarm_hidden + [
        'features.deep_research',
        'features.deep_research.models.openai_compat',
        'features.deep_research.models.router',
        'features.deep_research.models.luckyd',
        'features.deep_research.models.mock',
        'features.deep_research.models.gemini',
        'langgraph',
        'langgraph.graph',
        'langchain_core',
        'langchain_core.runnables',
        'langchain_core.runnables.base',
        'uuid_utils',
        'uuid_utils._uuid_utils',
        'google.genai',
        'ddgs',
        'duckduckgo_search',
        'trafilatura',
        'bs4',
        'lxml',
        'tldextract',
        'tenacity',
        'tools.deep_research_tool',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # rich: unused by the browser. PIL: only used by the make_icon.py dev
    # tool, and Pillow 12.3's Image.py crashes Python 3.10.0's dis during
    # modulegraph scanning (IndexError) - excluding it fixes the build.
    # pygame: same dis crash (tuple index out of range) when some installed
    # package's conditional import drags it into modulegraph; the browser
    # never imports it. werkzeug/flask: same story (transitively scanned via
    # try/except imports, crash in werkzeug/http.py on py3.10.0).
    excludes=['rich', 'PIL', 'pygame', 'werkzeug', 'flask'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='LuckyDBrowser',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='version_info.txt',
    icon=['assets\\professional_icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    # Never UPX the ConPTY sidekicks — they are spawned as live processes /
    # loaded as DLLs by the terminal bridge; keep their bytes pristine.
    upx_exclude=['winpty*', 'conpty.dll', 'OpenConsole.exe', '_winpty*'],
    name='LuckyDBrowser',
)
