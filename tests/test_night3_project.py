"""Night-3 tests: project/detector.py.

Covers detect() end-to-end on synthetic projects (Python, JS, Rust),
language/framework/version/package-manager/test/lint detection, entry
points, flags, the missing-path early return, and ProjectInfo.to_prompt.
"""

from __future__ import annotations

import json

import pytest

from project.detector import ProjectDetector
from project.types import ProjectInfo


@pytest.fixture()
def detector():
    return ProjectDetector()


def _write(root, name, content=""):
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def test_detect_missing_path_returns_empty_info(detector, tmp_path):
    info = detector.detect(tmp_path / "nope")
    assert isinstance(info, ProjectInfo)
    assert info.is_empty()
    assert info.root.endswith("nope")


def test_detect_empty_dir(detector, tmp_path):
    info = detector.detect(tmp_path)
    assert info.is_empty()
    assert info.name == tmp_path.name
    assert info.total_dirs >= 1


def test_detect_python_project(detector, tmp_path):
    _write(tmp_path, "main.py", "print('hi')\n")
    _write(tmp_path, "requirements.txt", "pytest>=8.0\nflask\n")
    _write(tmp_path, "pyproject.toml", '[project]\nrequires-python = ">=3.10"\n[tool.ruff]\n')
    _write(tmp_path, "README.md", "# hi\n")
    _write(tmp_path, "Dockerfile", "FROM python\n")
    info = detector.detect(tmp_path)
    assert info.language == "Python"
    assert info.language_version == ">=3.10"
    assert info.framework == "Flask"
    assert info.package_manager == "pip"
    assert info.test_framework == "pytest"
    assert info.linter == "ruff" and info.formatter == "ruff"
    assert info.entry_point == "main.py"
    assert "pyproject.toml" in info.key_files
    assert info.has_readme is True
    assert info.has_docker is True
    assert not info.is_empty()


def test_detect_python_django_via_pyproject(detector, tmp_path):
    _write(tmp_path, "manage.py", "")
    _write(tmp_path, "pyproject.toml", '[project]\ndependencies = ["django"]\n')
    info = detector.detect(tmp_path)
    assert info.language == "Python"
    assert info.framework == "Django"
    assert info.entry_point == "manage.py"
    assert info.package_manager == "pip"


def test_detect_pipenv_and_flake8(detector, tmp_path):
    _write(tmp_path, "app.py", "")
    _write(tmp_path, "Pipfile", "[[source]]\n")
    _write(tmp_path, ".flake8", "[flake8]\n")
    info = detector.detect(tmp_path)
    assert info.package_manager == "pipenv"
    assert info.linter == "flake8"
    assert info.formatter == ""


def test_detect_javascript_project(detector, tmp_path):
    _write(tmp_path, "index.js", "console.log(1)\n")
    _write(
        tmp_path,
        "package.json",
        json.dumps(
            {
                "name": "x",
                "main": "index.js",
                "engines": {"node": ">=20"},
                "dependencies": {"express": "^4"},
                "devDependencies": {"jest": "^29"},
            }
        ),
    )
    _write(tmp_path, "yarn.lock", "")
    _write(tmp_path, ".eslintrc.json", "{}")
    info = detector.detect(tmp_path)
    assert info.language == "JavaScript"
    assert info.language_version == ">=20"
    assert info.framework == "Express.js"
    assert info.package_manager == "yarn"
    assert info.test_framework == "jest"
    assert info.linter == "eslint"
    assert info.entry_point == "index.js"


def test_detect_typescript_project(detector, tmp_path):
    _write(tmp_path, "src/index.ts", "")
    _write(
        tmp_path,
        "package.json",
        json.dumps({"name": "x", "dependencies": {"react": "^19"}}),
    )
    _write(tmp_path, "tsconfig.json", "{}")
    _write(tmp_path, "pnpm-lock.yaml", "")
    info = detector.detect(tmp_path)
    assert info.language == "TypeScript"
    assert info.framework == "React"
    assert info.package_manager == "pnpm"
    assert info.entry_point == "src/index.ts"


def test_detect_rust_project(detector, tmp_path):
    _write(tmp_path, "src/main.rs", "fn main() {}\n")
    _write(tmp_path, "Cargo.toml", '[package]\nname="x"\n[dependencies]\ntokio = "1"\n')
    info = detector.detect(tmp_path)
    assert info.language == "Rust"
    assert info.framework == "Tokio"
    assert info.build_system == "cargo"
    assert info.package_manager == "cargo"
    assert info.test_framework == "cargo test"
    assert info.entry_point == "src/main.rs"


def test_detect_go_project(detector, tmp_path):
    _write(tmp_path, "main.go", "package main\n")
    _write(tmp_path, "go.mod", "module x\nrequire github.com/gin-gonic/gin v1\n")
    info = detector.detect(tmp_path)
    assert info.language == "Go"
    assert info.framework == "Gin"
    assert info.build_system == "go mod"
    assert info.test_framework == "go test"


def test_detect_skips_excluded_dirs(detector, tmp_path):
    # Files under venv/node_modules must not skew detection.
    _write(tmp_path, "venv/lib/huge.py", "x" * 10000)
    _write(tmp_path, "node_modules/dep/index.js", "")
    _write(tmp_path, "app.py", "")
    info = detector.detect(tmp_path)
    assert info.language == "Python"
    assert info.total_files == 1


def test_detect_ci_and_docs_flags(detector, tmp_path):
    _write(tmp_path, "app.py", "")
    _write(tmp_path, ".github/workflows/ci.yml", "")
    _write(tmp_path, "docs/guide.md", "")
    info = detector.detect(tmp_path)
    assert info.has_ci is True
    assert info.has_docs is True
    assert info.has_docker is False


def test_detect_has_tests_flag(detector, tmp_path):
    _write(tmp_path, "app.py", "")
    assert detector.detect(tmp_path).has_tests is False
    _write(tmp_path, "tests/test_app.py", "")
    assert detector.detect(tmp_path).has_tests is True


def test_detect_entry_point_unknown_language(detector, tmp_path):
    _write(tmp_path, "notes.txt", "")
    info = detector.detect(tmp_path)
    assert info.language == ""
    assert info.entry_point == ""


def test_to_prompt_contents(detector, tmp_path):
    _write(tmp_path, "main.py", "")
    _write(tmp_path, "requirements.txt", "pytest\n")
    info = detector.detect(tmp_path)
    prompt = info.to_prompt()
    assert "## Project:" in prompt
    assert "Language: Python" in prompt
    assert "Tests: pytest" in prompt
    assert "Entry: main.py" in prompt


def test_project_info_defaults():
    info = ProjectInfo()
    assert info.is_empty()
    assert info.to_prompt().startswith("## Project: unknown")
