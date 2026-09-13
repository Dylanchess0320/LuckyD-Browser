"""Night-5 tests: core/project_intelligence.py (was 0% covered).

Static analysis logic on synthetic project trees: framework detection across
all ecosystems, dependency analysis (unused/outdated detection), AST + regex
pattern detection, structure analysis, recommendations, and report rendering.
"""

from __future__ import annotations

import json

import pytest

from core.project_intelligence import (
    ProjectIntelligence,
    ProjectReport,
)


@pytest.fixture()
def pi():
    return ProjectIntelligence()


def _write(root, name, content=""):
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


# ── framework detection ──────────────────────────────────────────────────


def test_detect_js_framework_from_package_json(pi, tmp_path):
    _write(
        tmp_path,
        "package.json",
        json.dumps(
            {
                "dependencies": {"react": "^18", "express": "^4"},
                "devDependencies": {"tailwindcss": "^3"},
            }
        ),
    )
    hits = pi.detect_frameworks(tmp_path)
    by_name = {h.name: h for h in hits}
    assert by_name["React"].confidence == 0.95
    assert by_name["React"].source == "package.json"
    assert by_name["React"].ecosystem == "javascript"
    assert by_name["Express"].confidence == 0.95
    # devDependency -> lower confidence
    assert by_name["Tailwind CSS"].confidence == 0.85


def test_detect_py_framework_from_requirements(pi, tmp_path):
    _write(tmp_path, "requirements.txt", "fastapi>=0.100\nuvicorn\npytest\n")
    hits = pi.detect_frameworks(tmp_path)
    by_name = {h.name: h for h in hits}
    assert by_name["FastAPI"].confidence == 0.9
    assert by_name["FastAPI"].source == "requirements.txt"


def test_detect_py_framework_from_pyproject_poetry(pi, tmp_path):
    _write(
        tmp_path,
        "pyproject.toml",
        '[tool.poetry]\nname = "x"\ndependencies = ["django>=4"]\n',
    )
    hits = pi.detect_frameworks(tmp_path)
    by_name = {h.name: h for h in hits}
    assert "Django" in by_name
    assert by_name["Poetry"].confidence == 0.95


def test_detect_dotnet_framework_from_csproj(pi, tmp_path):
    _write(
        tmp_path,
        "app.csproj",
        '<Project Sdk="Microsoft.NET.Sdk.Web">\n'
        "  <ItemGroup>\n"
        '    <PackageReference Include="Microsoft.AspNetCore.Mvc" Version="8.0" />\n'
        '    <PackageReference Include="xunit" Version="2.5" />\n'
        "  </ItemGroup>\n</Project>\n",
    )
    hits = pi.detect_frameworks(tmp_path)
    by_name = {h.name: h for h in hits}
    assert by_name[".NET"].confidence == 0.95
    assert by_name["ASP.NET Core"].ecosystem == "dotnet"
    assert by_name["xUnit"].name == "xUnit"


def test_detect_go_rust_ruby_php_frameworks(pi, tmp_path):
    _write(tmp_path, "go.mod", "module x\nrequire github.com/gin-gonic/gin v1.9\n")
    _write(
        tmp_path,
        "Cargo.toml",
        '[dependencies]\ntokio = "1"\nserde = { version = "1", features = ["derive"] }\n',
    )
    _write(tmp_path, "Gemfile", "source 'https://rubygems.org'\ngem 'rails', '~> 7.0'\n")
    _write(
        tmp_path,
        "composer.json",
        json.dumps({"require": {"laravel/framework": "^10"}}),
    )
    hits = pi.detect_frameworks(tmp_path)
    by_name = {h.name: h for h in hits}
    assert by_name["Gin"].ecosystem == "go"
    assert by_name["Tokio"].ecosystem == "rust"
    assert by_name["Ruby on Rails"].ecosystem == "ruby"
    assert by_name["Laravel"].ecosystem == "php"


def test_framework_dedupe_keeps_highest_confidence(pi, tmp_path):
    # fastapi in both requirements.txt (0.9) and pyproject.toml (0.85)
    _write(tmp_path, "requirements.txt", "fastapi\n")
    _write(tmp_path, "pyproject.toml", 'dependencies = ["fastapi"]\n')
    hits = pi.detect_frameworks(tmp_path)
    fastapi = [h for h in hits if h.name == "FastAPI"]
    assert len(fastapi) == 1
    assert fastapi[0].confidence == 0.9
    assert fastapi[0].source == "requirements.txt"


def test_detect_frameworks_invalid_json_tolerated(pi, tmp_path):
    _write(tmp_path, "package.json", "{not json")
    _write(tmp_path, "composer.json", "{bad")
    hits = pi.detect_frameworks(tmp_path)
    assert hits == []


def test_detect_frameworks_empty_dir(pi, tmp_path):
    assert pi.detect_frameworks(tmp_path) == []


# ── dependency analysis ──────────────────────────────────────────────────


def test_dependencies_declared_and_unused(pi, tmp_path):
    _write(tmp_path, "requirements.txt", "flask>=2.0\nrequests\ntotally-unused-pkg\n")
    _write(tmp_path, "app.py", "import flask\napp = flask.Flask(__name__)\n")
    info = pi.analyze_dependencies(tmp_path)
    assert info.manifest_files == ["requirements.txt"]
    assert "flask" in info.declared
    # requests has no pin -> possibly outdated; unused too
    assert "requests" in info.possibly_outdated
    assert "requests" in info.unused
    assert "totally-unused-pkg" in info.unused
    assert "flask" not in info.unused
    assert "flask" not in info.possibly_outdated  # pinned with >=


def test_dependencies_unpinned_flagged(pi, tmp_path):
    _write(tmp_path, "requirements.txt", "requests\nflask==2.3.0\n# comment\n-r other.txt\n")
    info = pi.analyze_dependencies(tmp_path)
    assert "requests" in info.possibly_outdated
    assert "flask" not in info.possibly_outdated


def test_dependencies_pyproject_declared(pi, tmp_path):
    _write(tmp_path, "pyproject.toml", 'dependencies = ["httpx", "pytest"]\n')
    _write(tmp_path, "main.py", "import httpx\n")
    info = pi.analyze_dependencies(tmp_path)
    assert "pyproject.toml" in info.manifest_files
    assert "httpx" in info.declared
    assert "pytest" in info.declared
    assert "pytest" in info.unused  # not imported, not a dev-tool exemption
    assert "httpx" not in info.unused


def test_dependencies_dev_packages_exempt(pi, tmp_path):
    _write(tmp_path, "requirements.txt", "black\nmypy\nisort\n")
    info = pi.analyze_dependencies(tmp_path)
    assert info.unused == []


def test_dependencies_import_from_and_dotted_import_count(pi, tmp_path):
    _write(tmp_path, "requirements.txt", "numpy\nsqlalchemy\n")
    _write(
        tmp_path,
        "m.py",
        "from numpy import array\nimport sqlalchemy.orm\nimport numpy.linalg\n",
    )
    info = pi.analyze_dependencies(tmp_path)
    assert "numpy" not in info.unused
    assert "sqlalchemy" not in info.unused


def test_dependencies_package_json_declared(pi, tmp_path):
    _write(
        tmp_path,
        "package.json",
        json.dumps({"dependencies": {"react": "^18"}, "devDependencies": {"vite": "^5"}}),
    )
    info = pi.analyze_dependencies(tmp_path)
    assert "package.json" in info.manifest_files
    assert "react" in info.declared
    assert "vite" in info.declared


def test_dependencies_declared_sorted(pi, tmp_path):
    _write(tmp_path, "requirements.txt", "zebra-pkg\napple-pkg\n")
    info = pi.analyze_dependencies(tmp_path)
    assert info.declared == sorted(info.declared, key=str.lower)


# ── pattern detection ────────────────────────────────────────────────────


def test_detect_singleton_pattern(pi, tmp_path):
    _write(
        tmp_path,
        "s.py",
        "class Config:\n"
        "    _instance = None\n"
        "    def __new__(cls):\n"
        "        if cls._instance is None:\n"
        "            cls._instance = super().__new__(cls)\n"
        "        return cls._instance\n",
    )
    hits = pi.detect_patterns(tmp_path)
    singletons = [h for h in hits if h.pattern == "Singleton"]
    assert len(singletons) == 1
    assert singletons[0].file == "s.py"
    assert singletons[0].line == 1


def test_detect_factory_pattern(pi, tmp_path):
    _write(
        tmp_path,
        "f.py",
        "class WidgetFactory:\n"
        "    def create_widget(self):\n"
        "        return object()\n"
        "\n\ndef make_gadget():\n"
        "    return object()\n",
    )
    hits = pi.detect_patterns(tmp_path)
    factories = [h for h in hits if h.pattern == "Factory"]
    assert len(factories) == 2  # class method + module-level function


def test_detect_mvc_and_observer_patterns(pi, tmp_path):
    _write(
        tmp_path,
        "m.py",
        "class UserController:\n"
        "    pass\n"
        "\n\nclass EventBus:\n"
        "    def subscribe(self, fn):\n"
        "        pass\n"
        "    def emit(self, evt):\n"
        "        pass\n",
    )
    hits = pi.detect_patterns(tmp_path)
    mvc = [h for h in hits if h.pattern == "MVC"]
    observer = [h for h in hits if h.pattern == "Observer"]
    assert any("UserController" in h.detail for h in mvc)
    assert len(observer) == 1
    assert observer[0].file == "m.py"


def test_detect_regex_patterns_singleton_observer(pi, tmp_path):
    _write(
        tmp_path,
        "r.py",
        "bus = Bus()  # singleton\nbutton.on('click', handler)\nsocket.subscribe(topics)\n",
    )
    hits = pi.detect_patterns(tmp_path)
    patterns = {h.pattern for h in hits}
    assert "Singleton" in patterns
    assert "Observer" in patterns


def test_detect_patterns_syntax_error_tolerated(pi, tmp_path):
    _write(tmp_path, "bad.py", "def broken(:\n")
    _write(
        tmp_path,
        "good.py",
        "class Config:\n"
        "    _instance = None\n"
        "    def __new__(cls):\n"
        "        if cls._instance is None:\n"
        "            cls._instance = super().__new__(cls)\n"
        "        return cls._instance\n",
    )
    hits = pi.detect_patterns(tmp_path)
    # No crash on the unparseable file; the AST pass contributes nothing for
    # it and the regex pass finds no markers in "def broken(:".
    assert all(h.file != "bad.py" for h in hits)
    # ...while a valid file is still detected normally.
    assert any(h.file == "good.py" and h.pattern == "Singleton" for h in hits)


def test_detect_patterns_skips_venv(pi, tmp_path):
    venv = tmp_path / ".venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "inner.py").write_text("class DeepController:\n    pass\n", encoding="utf-8")
    hits = pi.detect_patterns(tmp_path)
    assert all(".venv" not in h.file for h in hits)


def test_detect_patterns_capped_at_200(pi, tmp_path):
    for i in range(250):
        _write(tmp_path, f"m{i}.py", f"class C{i}Controller:\n    pass\n")
    hits = pi.detect_patterns(tmp_path)
    assert len(hits) <= 200


# ── structure analysis + tech stack ──────────────────────────────────────


def test_structure_stats_loc_and_classification(pi, tmp_path):
    src = tmp_path / "src"
    tests_d = tmp_path / "tests"
    src.mkdir()
    tests_d.mkdir()
    (src / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    (src / "b.js").write_text("const x = 1;\n", encoding="utf-8")
    (tests_d / "t.py").write_text("pass\n", encoding="utf-8")
    (tmp_path / "icon.png").write_bytes(b"\x89PNG\r\n")
    stats = pi._analyze_structure(tmp_path)
    assert stats.file_count == 3  # png skipped as binary
    assert stats.total_loc == 4
    assert stats.loc_by_lang["Python"] == 3
    assert stats.dir_classification["src"] == "source"
    assert stats.dir_classification["tests"] == "tests"
    assert stats.max_depth == 1
    assert stats.largest_files[0] == ("src/a.py", 2) or stats.largest_files[0][1] == 2


def test_structure_deep_nesting(pi, tmp_path):
    deep = tmp_path
    for i in range(10):
        deep = deep / f"d{i}"
    deep.mkdir(parents=True)
    (deep / "f.py").write_text("x = 1\n", encoding="utf-8")
    stats = pi._analyze_structure(tmp_path)
    assert stats.max_depth == 10


def test_classify_dirs_kinds(pi, tmp_path):
    for d in ("docs", "scripts", "assets", ".github", "examples", "data", "misc"):
        (tmp_path / d).mkdir()
    mapping = pi._classify_dirs(tmp_path)
    assert mapping["docs"] == "docs"
    assert mapping["scripts"] == "tooling"
    assert mapping["assets"] == "assets"
    assert mapping[".github"] == "ci"
    assert mapping["examples"] == "examples"
    assert mapping["data"] == "data"
    assert mapping["misc"] == "other"


def test_tech_stack_by_file_count(pi, tmp_path):
    _write(tmp_path, "a.py", "")
    _write(tmp_path, "b.py", "")
    _write(tmp_path, "c.js", "")
    stats = pi._analyze_structure(tmp_path)
    stack = pi._detect_tech_stack(stats)
    assert stack == {"Python": 2, "JavaScript": 1}


# ── recommendations ──────────────────────────────────────────────────────


def test_recommendations_empty_project(pi, tmp_path):
    recs = pi.get_recommendations(tmp_path, None)
    joined = " ".join(recs)
    assert "README" in joined
    assert "tests/" in joined
    assert ".gitignore" in joined
    assert "CI" in joined
    assert "LICENSE" in joined


def test_recommendations_suppressed_when_present(pi, tmp_path):
    _write(tmp_path, "README.md", "# x\n")
    (tmp_path / "tests").mkdir()
    _write(tmp_path, ".gitignore", "")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    _write(tmp_path, ".github/workflows/ci.yml", "")
    _write(tmp_path, "LICENSE", "MIT\n")
    recs = pi.get_recommendations(tmp_path, None)
    assert recs == []


def test_recommendations_giant_file(pi, tmp_path):
    big = tmp_path / "big.py"
    big.write_text("\n".join(f"x{i} = {i}" for i in range(1200)) + "\n")
    report = pi.analyze_project(tmp_path)
    assert any("giant file" in r and "big.py" in r for r in report.recommendations)


def test_recommendations_deep_nesting(pi, tmp_path):
    deep = tmp_path
    for i in range(10):
        deep = deep / f"d{i}"
    deep.mkdir(parents=True)
    (deep / "f.py").write_text("x = 1\n", encoding="utf-8")
    report = pi.analyze_project(tmp_path)
    assert any("nesting" in r for r in report.recommendations)


def test_recommendations_with_report(pi, tmp_path):
    report = pi.analyze_project(tmp_path)
    recs = pi.get_recommendations(tmp_path, report)
    # empty tmp dir: no dependencies at all, so no unused-deps recommendation
    assert not any("possibly unused" in r for r in recs)
    # ...but one appears once the report actually has unused dependencies
    report.dependencies.unused.append("requests")
    recs2 = pi.get_recommendations(tmp_path, report)
    assert any("1 possibly unused dependencies" in r for r in recs2)


# ── end-to-end + rendering ────────────────────────────────────────────────


def test_analyze_project_end_to_end(pi, tmp_path):
    _write(tmp_path, "README.md", "# demo\n")
    _write(tmp_path, "requirements.txt", "flask\nunused-pkg\n")
    _write(
        tmp_path,
        "app.py",
        "import flask\n\n\nclass HomeController:\n    pass\n",
    )
    report = pi.analyze_project(tmp_path)
    assert isinstance(report, ProjectReport)
    assert any(h.name == "Flask" for h in report.frameworks)
    assert "unused-pkg" in report.dependencies.unused
    assert any(h.pattern == "MVC" for h in report.patterns)
    assert report.tech_stack.get("Python") == 1
    assert report.structure.file_count >= 3
    assert not any("README" in r for r in report.recommendations)
    md = report.to_markdown()
    assert "# Project Intelligence Report" in md
    assert "Flask" in md
    assert "## Tech Stack" in md
    data = report.to_dict()
    assert data["root"] == str(tmp_path.resolve())
    assert isinstance(data["frameworks"], list)
    assert data["structure"]["file_count"] == report.structure.file_count


def test_to_markdown_empty_sections(pi):
    report = ProjectReport(root="/nowhere")
    md = report.to_markdown()
    assert "- None detected" in md
    assert "looks good" in md
    data = report.to_dict()
    assert data["frameworks"] == []
    assert data["recommendations"] == []


def test_max_files_cap(pi, tmp_path):
    pi_small = ProjectIntelligence(max_files=5)
    for i in range(20):
        _write(tmp_path, f"f{i}.py", "x = 1\n")
    stats = pi_small._analyze_structure(tmp_path)
    assert stats.file_count == 5
