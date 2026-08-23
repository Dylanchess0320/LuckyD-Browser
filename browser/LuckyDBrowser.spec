# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all

# pywinpty powers the in-browser terminal (browser_core.terminal_server does
# a lazy "from winpty import PTY"), so modulegraph never sees it — collect the
# whole package explicitly: _winpty.pyd plus the ConPTY native sidekicks
# (conpty.dll, OpenConsole.exe, winpty.dll, winpty-agent.exe) that must sit
# beside it in _internal/winpty/. Without these the /terminal tab's WS->PTY
# bridge dies on every connection with "terminal failed to start".
wp_datas, wp_binaries, wp_hiddenimports = collect_all('winpty')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=wp_binaries,
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
           ('../skills', 'skills'),
           # Ship browser_core (esp. cline_session.py) as a data folder so the
           # bundled core/providers.py can locate it at browser/browser_core/
           # relative to _internal (its __file__.parent.parent) in the frozen app.
           ('browser_core', 'browser/browser_core'),
           ('../luckyd-code.exe', '.'),
           # Ship the clean template (never the dev .env — it holds real keys),
           # plus a ready-made _internal/.env so the bundled harness/terminal
           # exes default to free local Ollama on end-user machines.
           ('../.env.example', '.env.example'),
           ('installer/env/.env', '.'),
           # Bundled Deck Studio (Marp pipeline UI + decks + themes). The
           # TileRegistry autostarts it from %APPDIR%\studio; ai.js falls back
           # to the app-injected GOOGLE_API_KEY, so no secret ships in here.
           ('studio', 'studio')] + wp_datas,
    # websockets + winpty are imported lazily (CDP driver / screenshots /
    # terminal bridge) - pin them. assets/ ships recursively, including
    # assets/terminal/ (the vendored xterm.js page the /terminal tab needs).
    hiddenimports=['websockets'] + wp_hiddenimports,
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
    icon=['assets\\icon.ico'],
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
