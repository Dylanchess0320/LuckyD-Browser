# -*- mode: python ; coding: utf-8 -*-
#
# Build the FIXED LuckyD Code harness exe:
#   python -m PyInstaller --noconfirm --clean luckyd-code.spec
#
# Produces dist\\luckyd-code.exe — a one-file backend that serves the Harness HQ
# (--web --port 8000) using the FIXED core/llm_client.py (no more
# "Illegal header value b'Bearer '").
#
# Heavy/optional deps (torch, playwright, onnxruntime, rich, prompt_toolkit,
# websockets, PIL, mss, numpy) are excluded; the backend guards them and the HQ
# core path needs only httpx.

block_cipher = None

a = Analysis(
    ['web_server.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('core', 'core'),
        ('llm', 'llm'),
        ('memory', 'memory'),
        ('tools', 'tools'),
        ('project', 'project'),
        ('skills', 'skills'),
        ('agent.py', '.'),
        ('config.py', '.'),
        ('model_resolver.py', '.'),
        ('ui.py', '.'),
        # Bundle the Cline session reader so the frozen exe can use ClinePass
        # (reads ~/.cline/data/settings/providers.json) with no API key.
        ('browser/browser_core/cline_session.py', '.'),
        # Never embed a developer's real .env/API keys in a distributable harness.
        ('.env.example', '.env.example'),
    ],
    hiddenimports=[
        'httpx', 'httpcore', 'h11', 'certifi', 'idna', 'sniffio', 'anyio',
        'cline_session',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Exclude every heavy third-party dep the backend imports optionally — they
    # are all wrapped in try/except at runtime, so the harness still runs. This
    # also avoids the PyInstaller/modulegraph "IndexError: tuple index out of
    # range" on Python 3.10 that one of these packages triggers during AST
    # scanning (the same reason the browser spec excludes rich/PIL).
    excludes=[
        # semantic/ML
        'torch', 'torchaudio', 'torchvision', 'onnxruntime', 'transformers',
        'numpy', 'scipy', 'sklearn', 'pandas', 'matplotlib', 'sentence_transformers',
        # browser automation
        'playwright', 'websockets', 'browser_use', 'selenium',
        # desktop / vision
        'PIL', 'mss', 'pyautogui', 'pygetwindow', 'pyperclip', 'win10toast',
        # CLI front-end (not used by --web)
        'rich', 'prompt_toolkit', 'wcwidth', 'colorama',
        # misc optional tools
        'yaml', 'jedi', 'ddgs', 'duckduckgo_search', 'requests', 'aiohttp',
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
    name='luckyd-code',
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
