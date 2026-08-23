"""
Project intelligence — static analysis of a project tree.

Detects frameworks, analyzes dependencies, recognizes code patterns,
classifies project structure, detects tech stack, and produces
best-practice recommendations. Stdlib only.
"""

from __future__ import annotations

import ast
import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Constants ──────────────────────────────────────────────────────────

_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".idea",
    ".vscode",
    "target",
    "bin",
    "obj",
    ".next",
    ".nuxt",
    "coverage",
    ".cargo",
    "vendor",
}

_LANG_BY_EXT = {
    ".py": "Python",
    ".js": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".jsx": "JavaScript",
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".java": "Java",
    ".kt": "Kotlin",
    ".c": "C",
    ".h": "C/C++",
    ".cpp": "C++",
    ".hpp": "C++",
    ".swift": "Swift",
    ".lua": "Lua",
    ".sh": "Shell",
    ".ps1": "PowerShell",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".sql": "SQL",
    ".vue": "Vue",
    ".svelte": "Svelte",
}

# framework name -> (manifest file, json key path or regex, confidence)
_JS_FRAMEWORK_HINTS = {
    "react": "React",
    "react-dom": "React",
    "next": "Next.js",
    "vue": "Vue",
    "nuxt": "Nuxt",
    "@angular/core": "Angular",
    "svelte": "Svelte",
    "express": "Express",
    "fastify": "Fastify",
    "nest": "NestJS",
    "@nestjs/core": "NestJS",
    "gatsby": "Gatsby",
    "electron": "Electron",
    "tailwindcss": "Tailwind CSS",
}

_PY_FRAMEWORK_HINTS = {
    "django": "Django",
    "flask": "Flask",
    "fastapi": "FastAPI",
    "starlette": "Starlette",
    "tornado": "Tornado",
    "sqlalchemy": "SQLAlchemy",
    "pytest": "pytest",
    "celery": "Celery",
    "pydantic": "Pydantic",
    "numpy": "NumPy",
    "pandas": "pandas",
    "torch": "PyTorch",
    "tensorflow": "TensorFlow",
    "click": "Click",
    "rich": "Rich",
    "httpx": "httpx",
    "requests": "requests",
    "aiohttp": "aiohttp",
}

_RUBY_FRAMEWORK_HINTS = {
    "rails": "Ruby on Rails",
    "sinatra": "Sinatra",
    "rspec": "RSpec",
    "sidekiq": "Sidekiq",
}

_PHP_FRAMEWORK_HINTS = {
    "laravel/framework": "Laravel",
    "symfony/framework-bundle": "Symfony",
    "phpunit/phpunit": "PHPUnit",
    "slim/slim": "Slim",
}

_GO_FRAMEWORK_HINTS = {
    "github.com/gin-gonic/gin": "Gin",
    "github.com/gorilla/mux": "Gorilla Mux",
    "github.com/labstack/echo": "Echo",
}

_RUST_FRAMEWORK_HINTS = {
    "actix-web": "Actix Web",
    "rocket": "Rocket",
    "tokio": "Tokio",
    "axum": "Axum",
    "serde": "Serde",
}

_CI_FILES = [
    ".github/workflows",
    ".gitlab-ci.yml",
    "azure-pipelines.yml",
    ".circleci/config.yml",
    "Jenkinsfile",
    ".travis.yml",
    "appveyor.yml",
]

_GIANT_FILE_LINES = 1000
_DEEP_NESTING_DEPTH = 8

# Extensions we treat as text for LOC counting; binaries are skipped.
_BINARY_EXTS = {
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".bin",
    ".dat",
    ".db",
    ".sqlite",
    ".zip",
    ".gz",
    ".tar",
    ".7z",
    ".rar",
    ".whl",
    ".jar",
    ".war",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".svg",
    ".webp",
    ".bmp",
    ".mp3",
    ".mp4",
    ".wav",
    ".avi",
    ".mov",
    ".mkv",
    ".pdf",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".eot",
    ".pyc",
    ".pyo",
    ".class",
    ".o",
    ".a",
    ".lib",
    ".msi",
    ".dmg",
    ".iso",
    ".snap",
}

_REQ_LINE_RE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*(?:==|>=|<=|~=|!=|>|<)?")
_IMPORT_FROM_RE = re.compile(r"^\s*(?:from|import)\s+([A-Za-z0-9_]+)", re.M)


# ── Result dataclasses ────────────────────────────────────────────────


@dataclass
class FrameworkHit:
    name: str
    confidence: float  # 0.0 - 1.0
    source: str  # manifest file where detected
    ecosystem: str  # python, javascript, dotnet, go, rust, ruby, php

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "confidence": round(self.confidence, 2),
            "source": self.source,
            "ecosystem": self.ecosystem,
        }


@dataclass
class DependencyInfo:
    declared: list[str] = field(default_factory=list)
    unused: list[str] = field(default_factory=list)
    possibly_outdated: list[str] = field(default_factory=list)
    manifest_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "declared": self.declared,
            "unused": self.unused,
            "possibly_outdated": self.possibly_outdated,
            "manifest_files": self.manifest_files,
        }


@dataclass
class PatternHit:
    pattern: str
    file: str
    line: int
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern,
            "file": self.file,
            "line": self.line,
            "detail": self.detail,
        }


@dataclass
class StructureStats:
    file_count: int = 0
    total_loc: int = 0
    files_by_ext: dict[str, int] = field(default_factory=dict)
    loc_by_lang: dict[str, int] = field(default_factory=dict)
    largest_files: list[tuple[str, int]] = field(default_factory=list)
    dir_classification: dict[str, str] = field(default_factory=dict)
    max_depth: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_count": self.file_count,
            "total_loc": self.total_loc,
            "files_by_ext": self.files_by_ext,
            "loc_by_lang": self.loc_by_lang,
            "largest_files": [{"file": f, "lines": n} for f, n in self.largest_files],
            "dir_classification": self.dir_classification,
            "max_depth": self.max_depth,
        }


@dataclass
class ProjectReport:
    root: str
    frameworks: list[FrameworkHit] = field(default_factory=list)
    dependencies: DependencyInfo = field(default_factory=DependencyInfo)
    patterns: list[PatternHit] = field(default_factory=list)
    structure: StructureStats = field(default_factory=StructureStats)
    recommendations: list[str] = field(default_factory=list)
    tech_stack: dict[str, int] = field(default_factory=dict)  # lang -> file count

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "frameworks": [f.to_dict() for f in self.frameworks],
            "dependencies": self.dependencies.to_dict(),
            "patterns": [p.to_dict() for p in self.patterns],
            "structure": self.structure.to_dict(),
            "recommendations": self.recommendations,
            "tech_stack": self.tech_stack,
        }

    def to_markdown(self) -> str:
        """Render a human-readable markdown report."""
        lines: list[str] = ["# Project Intelligence Report", "", f"**Root:** `{self.root}`", ""]
        # Tech stack
        lines.append("## Tech Stack")
        total = sum(self.tech_stack.values()) or 1
        for lang, count in sorted(self.tech_stack.items(), key=lambda kv: -kv[1])[:10]:
            pct = count / total * 100
            lines.append(f"- **{lang}**: {count} files ({pct:.0f}%)")
        lines.append("")
        # Frameworks
        lines.append("## Detected Frameworks")
        if self.frameworks:
            for fw in sorted(self.frameworks, key=lambda f: -f.confidence):
                lines.append(
                    f"- **{fw.name}** ({fw.ecosystem}) — "
                    f"confidence {fw.confidence:.0%}, via `{fw.source}`"
                )
        else:
            lines.append("- None detected")
        lines.append("")
        # Structure
        s = self.structure
        lines.append("## Structure")
        lines.append(f"- Files: {s.file_count}")
        lines.append(f"- Total LOC: {s.total_loc:,}")
        lines.append(f"- Max directory depth: {s.max_depth}")
        if s.largest_files:
            lines.append("- Largest files:")
            for f, n in s.largest_files[:5]:
                lines.append(f"  - `{f}` ({n:,} lines)")
        if s.dir_classification:
            lines.append("- Directory classification:")
            for d, kind in sorted(s.dir_classification.items()):
                lines.append(f"  - `{d}/` -> {kind}")
        lines.append("")
        # Dependencies
        d = self.dependencies
        lines.append("## Dependencies")
        lines.append(f"- Declared: {len(d.declared)}")
        if d.unused:
            lines.append(f"- Possibly unused: {', '.join(d.unused[:15])}")
        if d.possibly_outdated:
            lines.append(
                f"- Possibly outdated (unpinned): " f"{', '.join(d.possibly_outdated[:15])}"
            )
        lines.append("")
        # Patterns
        lines.append("## Code Patterns")
        if self.patterns:
            by_pattern: dict[str, list[PatternHit]] = defaultdict(list)
            for p in self.patterns:
                by_pattern[p.pattern].append(p)
            for name, hits in sorted(by_pattern.items()):
                lines.append(f"- **{name}** ({len(hits)} occurrences)")
        else:
            lines.append("- None detected")
        lines.append("")
        # Recommendations
        lines.append("## Recommendations")
        if self.recommendations:
            for r in self.recommendations:
                lines.append(f"- {r}")
        else:
            lines.append("- Nothing to flag — looks good.")
        lines.append("")
        return "\n".join(lines)


# ── Main analyzer ─────────────────────────────────────────────────────


class ProjectIntelligence:
    """Static analyzer producing a ProjectReport for a project tree."""

    def __init__(self, max_files: int = 5000):
        self.max_files = max_files

    # ── public API ────────────────────────────────────────────────────

    def analyze_project(self, root: str | Path) -> ProjectReport:
        """Run all analyzers and return a complete ProjectReport."""
        root = Path(root).resolve()
        report = ProjectReport(root=str(root))
        report.frameworks = self.detect_frameworks(root)
        report.dependencies = self.analyze_dependencies(root)
        report.patterns = self.detect_patterns(root)
        report.structure = self._analyze_structure(root)
        report.tech_stack = self._detect_tech_stack(report.structure)
        report.recommendations = self.get_recommendations(root, report)
        return report

    def detect_frameworks(self, root: str | Path) -> list[FrameworkHit]:
        """Parse manifest files and return detected frameworks."""
        root = Path(root)
        hits: list[FrameworkHit] = []

        # package.json
        pkg = root / "package.json"
        if pkg.is_file():
            hits.extend(self._frameworks_from_package_json(pkg))
        # requirements.txt / pyproject.toml
        req = root / "requirements.txt"
        if req.is_file():
            hits.extend(self._frameworks_from_requirements(req))
        pyproject = root / "pyproject.toml"
        if pyproject.is_file():
            hits.extend(self._frameworks_from_pyproject(pyproject))
        # *.csproj
        for csproj in root.glob("*.csproj"):
            hits.extend(self._frameworks_from_csproj(csproj))
        # go.mod
        gomod = root / "go.mod"
        if gomod.is_file():
            hits.extend(self._frameworks_from_go_mod(gomod))
        # Cargo.toml
        cargo = root / "Cargo.toml"
        if cargo.is_file():
            hits.extend(self._frameworks_from_cargo(cargo))
        # Gemfile
        gemfile = root / "Gemfile"
        if gemfile.is_file():
            hits.extend(self._frameworks_from_gemfile(gemfile))
        # composer.json
        composer = root / "composer.json"
        if composer.is_file():
            hits.extend(self._frameworks_from_composer(composer))

        # Dedupe by name, keeping the highest confidence
        best: dict[str, FrameworkHit] = {}
        for h in hits:
            if h.name not in best or h.confidence > best[h.name].confidence:
                best[h.name] = h
        return list(best.values())

    def analyze_dependencies(self, root: str | Path) -> DependencyInfo:
        """Parse manifest files and detect unused/outdated Python deps."""
        root = Path(root)
        info = DependencyInfo()
        declared_py: list[str] = []

        # requirements.txt
        req = root / "requirements.txt"
        if req.is_file():
            info.manifest_files.append("requirements.txt")
            for line in req.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("-"):
                    continue
                m = _REQ_LINE_RE.match(line)
                if m:
                    pkg = m.group(1)
                    declared_py.append(pkg)
                    if not re.search(r"==|>=|<=|~=|!=", line):
                        info.possibly_outdated.append(pkg)

        # pyproject.toml — deps live in dependencies = [...] arrays
        pyproject = root / "pyproject.toml"
        if pyproject.is_file():
            info.manifest_files.append("pyproject.toml")
            text = pyproject.read_text(encoding="utf-8", errors="replace")
            for block in re.findall(
                r"(?:^|\n)\s*(?:dependencies|optional-dependencies(?:\.\w+)?)" r"\s*=\s*\[(.*?)\]",
                text,
                re.S,
            ):
                for m in re.finditer(r'"([A-Za-z0-9_.\-]+)(?:\s*[><=!~]=?[^"]*)?"', block):
                    pkg = m.group(1)
                    if pkg.lower() not in {p.lower() for p in declared_py}:
                        declared_py.append(pkg)

        # package.json
        pkg_json = root / "package.json"
        if pkg_json.is_file():
            info.manifest_files.append("package.json")
            try:
                data = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
                for section in ("dependencies", "devDependencies"):
                    declared_py.extend(data.get(section, {}).keys())
            except Exception:
                pass

        info.declared = sorted(set(declared_py), key=str.lower)

        # Unused Python deps: declared but never imported in any .py file
        imported = self._collect_python_imports(root)
        py_declared = {p for p in declared_py if not p.startswith("@") and "/" not in p}
        unused = []
        for pkg in sorted(py_declared, key=str.lower):
            norm = pkg.lower().replace("-", "_")
            if norm not in imported and pkg.lower() not in imported:
                # Ignore common meta/dev packages that aren't imported
                if norm in {
                    "pip",
                    "setuptools",
                    "wheel",
                    "build",
                    "twine",
                    "black",
                    "ruff",
                    "mypy",
                    "isort",
                    "flake8",
                    "pre_commit",
                    "pytest_cov",
                    "pytest_asyncio",
                }:
                    continue
                unused.append(pkg)
        info.unused = unused
        return info

    def detect_patterns(self, root: str | Path) -> list[PatternHit]:
        """Detect common design patterns in Python files via AST + regex."""
        root = Path(root)
        hits: list[PatternHit] = []
        for py_file in self._iter_files(root, {".py"}):
            rel = str(py_file.relative_to(root))
            try:
                source = py_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            hits.extend(self._ast_patterns(source, rel))
            hits.extend(self._regex_patterns(source, rel))
        return hits[:200]  # cap to keep report sane

    def get_recommendations(
        self, root: str | Path, report: ProjectReport | None = None
    ) -> list[str]:
        """Best-practice recommendations based on project layout."""
        root = Path(root)
        recs: list[str] = []
        lower_names = {p.name.lower() for p in root.iterdir() if p.is_file()}
        dir_names = {p.name.lower() for p in root.iterdir() if p.is_dir()}

        if not any(n.startswith("readme") for n in lower_names):
            recs.append("Add a README.md — project has no readme file.")
        if not ({"tests", "test", "spec"} & dir_names):
            recs.append("Add a tests/ directory — no test directory found.")
        if ".gitignore" not in lower_names:
            recs.append("Add a .gitignore — none found at project root.")
        if not any((root / ci).exists() for ci in _CI_FILES):
            recs.append("Add CI configuration (e.g. .github/workflows) — " "no CI config detected.")
        if "license" not in lower_names and "license.md" not in lower_names:
            recs.append("Add a LICENSE file — none found.")

        if report is not None:
            for f, n in report.structure.largest_files:
                if n > _GIANT_FILE_LINES:
                    recs.append(f"Consider splitting `{f}` — {n:,} lines " f"(giant file).")
            if report.structure.max_depth > _DEEP_NESTING_DEPTH:
                recs.append(
                    f"Directory nesting is {report.structure.max_depth} "
                    f"levels deep — consider flattening."
                )
            if report.dependencies.unused:
                recs.append(
                    f"Review {len(report.dependencies.unused)} possibly " f"unused dependencies."
                )
        return recs

    # ── framework parsers ─────────────────────────────────────────────

    def _frameworks_from_package_json(self, path: Path) -> list[FrameworkHit]:
        hits = []
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return hits
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        for dep, framework in _JS_FRAMEWORK_HINTS.items():
            if dep in deps:
                conf = 0.95 if dep in data.get("dependencies", {}) else 0.85
                hits.append(FrameworkHit(framework, conf, "package.json", "javascript"))
        return hits

    def _frameworks_from_requirements(self, path: Path) -> list[FrameworkHit]:
        hits = []
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        for dep, framework in _PY_FRAMEWORK_HINTS.items():
            if re.search(rf"^\s*{re.escape(dep)}\b", text, re.M):
                hits.append(FrameworkHit(framework, 0.9, "requirements.txt", "python"))
        return hits

    def _frameworks_from_pyproject(self, path: Path) -> list[FrameworkHit]:
        hits = []
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        for dep, framework in _PY_FRAMEWORK_HINTS.items():
            if f'"{dep}' in text or f"'{dep}" in text:
                hits.append(FrameworkHit(framework, 0.85, "pyproject.toml", "python"))
        # Poetry / build-system hints
        if "[tool.poetry]" in text:
            hits.append(FrameworkHit("Poetry", 0.95, "pyproject.toml", "python"))
        if "[tool.setuptools]" in text or "setuptools.build_meta" in text:
            hits.append(FrameworkHit("setuptools", 0.8, "pyproject.toml", "python"))
        return hits

    def _frameworks_from_csproj(self, path: Path) -> list[FrameworkHit]:
        hits = [FrameworkHit(".NET", 0.95, path.name, "dotnet")]
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r'Include="([^"]+)"', text):
            pkg = m.group(1)
            if "AspNetCore" in pkg:
                hits.append(FrameworkHit("ASP.NET Core", 0.95, path.name, "dotnet"))
            elif "EntityFrameworkCore" in pkg:
                hits.append(FrameworkHit("Entity Framework Core", 0.9, path.name, "dotnet"))
            elif "xunit" in pkg.lower():
                hits.append(FrameworkHit("xUnit", 0.9, path.name, "dotnet"))
        return hits

    def _frameworks_from_go_mod(self, path: Path) -> list[FrameworkHit]:
        hits = [FrameworkHit("Go modules", 0.9, "go.mod", "go")]
        text = path.read_text(encoding="utf-8", errors="replace")
        for dep, framework in _GO_FRAMEWORK_HINTS.items():
            if dep in text:
                hits.append(FrameworkHit(framework, 0.95, "go.mod", "go"))
        return hits

    def _frameworks_from_cargo(self, path: Path) -> list[FrameworkHit]:
        hits = [FrameworkHit("Cargo", 0.9, "Cargo.toml", "rust")]
        text = path.read_text(encoding="utf-8", errors="replace")
        for dep, framework in _RUST_FRAMEWORK_HINTS.items():
            if re.search(rf"^\s*{re.escape(dep)}\s*=", text, re.M):
                hits.append(FrameworkHit(framework, 0.95, "Cargo.toml", "rust"))
        return hits

    def _frameworks_from_gemfile(self, path: Path) -> list[FrameworkHit]:
        hits = []
        text = path.read_text(encoding="utf-8", errors="replace")
        for dep, framework in _RUBY_FRAMEWORK_HINTS.items():
            if re.search(rf"gem\s+['\"]{re.escape(dep)}['\"]", text):
                hits.append(FrameworkHit(framework, 0.95, "Gemfile", "ruby"))
        return hits

    def _frameworks_from_composer(self, path: Path) -> list[FrameworkHit]:
        hits = []
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return hits
        deps = {**data.get("require", {}), **data.get("require-dev", {})}
        for dep, framework in _PHP_FRAMEWORK_HINTS.items():
            if dep in deps:
                hits.append(FrameworkHit(framework, 0.95, "composer.json", "php"))
        return hits

    # ── dependency helpers ────────────────────────────────────────────

    def _collect_python_imports(self, root: Path) -> set[str]:
        """Top-level module names imported anywhere in the project's .py files."""
        imported: set[str] = set()
        for py_file in self._iter_files(root, {".py"}):
            try:
                text = py_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for m in _IMPORT_FROM_RE.finditer(text):
                imported.add(m.group(1).lower())
            # Also catch "import pkg.sub" root
            for m in re.finditer(r"^\s*import\s+([A-Za-z0-9_]+)\.", text, re.M):
                imported.add(m.group(1).lower())
        return imported

    # ── pattern detection ─────────────────────────────────────────────

    def _ast_patterns(self, source: str, rel_path: str) -> list[PatternHit]:
        hits: list[PatternHit] = []
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return hits
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = [self._name_of(b) for b in node.bases]
                methods = {
                    n.name
                    for n in node.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
                # Singleton: __new__ overridden or _instance class attr
                if "__new__" in methods or any(
                    isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "_instance" for t in n.targets)
                    for n in node.body
                ):
                    hits.append(
                        PatternHit("Singleton", rel_path, node.lineno, f"class {node.name}")
                    )
                # Factory: method named create*/make*/build*
                if any(re.match(r"(create|make|build)_", m) for m in methods):
                    hits.append(PatternHit("Factory", rel_path, node.lineno, f"class {node.name}"))
                # MVC hints
                if node.name.lower().endswith("controller"):
                    hits.append(
                        PatternHit("MVC", rel_path, node.lineno, f"Controller class {node.name}")
                    )
                elif node.name.lower().endswith("view") and "ABC" not in bases:
                    hits.append(PatternHit("MVC", rel_path, node.lineno, f"View class {node.name}"))
                # Observer: subscribe/notify/emit methods
                if methods & {
                    "subscribe",
                    "unsubscribe",
                    "notify",
                    "emit",
                    "add_listener",
                    "on_event",
                }:
                    hits.append(PatternHit("Observer", rel_path, node.lineno, f"class {node.name}"))
        return hits

    def _regex_patterns(self, source: str, rel_path: str) -> list[PatternHit]:
        hits: list[PatternHit] = []
        # Module-level singleton via decorator or global instance
        for m in re.finditer(r"^(\w+)\s*=\s*\1?\(?\)?\s*#\s*singleton", source, re.M | re.I):
            hits.append(
                PatternHit(
                    "Singleton", rel_path, source[: m.start()].count("\n") + 1, "global instance"
                )
            )
        # Observer via common event registration call
        for m in re.finditer(r"\.(on|subscribe|addEventListener)\(", source):
            hits.append(
                PatternHit(
                    "Observer",
                    rel_path,
                    source[: m.start()].count("\n") + 1,
                    f".{m.group(1)}() call",
                )
            )
        # Factory function
        for m in re.finditer(r"^def\s+(create|make|build)_\w+\(", source, re.M):
            hits.append(
                PatternHit(
                    "Factory",
                    rel_path,
                    source[: m.start()].count("\n") + 1,
                    f"function {m.group(0)[4:-1]}",
                )
            )
        return hits

    @staticmethod
    def _name_of(node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return ""

    # ── structure analysis ────────────────────────────────────────────

    def _analyze_structure(self, root: Path) -> StructureStats:
        stats = StructureStats()
        ext_counts: Counter[str] = Counter()
        loc_by_lang: Counter[str] = Counter()
        file_sizes: list[tuple[str, int]] = []
        max_depth = 0

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            rel_dir = os.path.relpath(dirpath, root)
            depth = 0 if rel_dir == "." else rel_dir.count(os.sep) + 1
            max_depth = max(max_depth, depth)
            for fname in filenames:
                fpath = Path(dirpath) / fname
                ext = fpath.suffix.lower()
                if ext in _BINARY_EXTS:
                    continue
                stats.file_count += 1
                ext_counts[ext] += 1
                lang = _LANG_BY_EXT.get(ext)
                try:
                    with open(fpath, encoding="utf-8", errors="replace") as fh:
                        loc = sum(1 for _ in fh)
                except Exception:
                    loc = 0
                stats.total_loc += loc
                if lang:
                    loc_by_lang[lang] += loc
                rel = str(fpath.relative_to(root))
                file_sizes.append((rel, loc))
                if stats.file_count >= self.max_files:
                    break
            if stats.file_count >= self.max_files:
                break

        stats.files_by_ext = dict(ext_counts.most_common(20))
        stats.loc_by_lang = dict(loc_by_lang.most_common(10))
        stats.largest_files = sorted(file_sizes, key=lambda kv: -kv[1])[:10]
        stats.max_depth = max_depth
        stats.dir_classification = self._classify_dirs(root)
        return stats

    def _classify_dirs(self, root: Path) -> dict[str, str]:
        """Classify top-level dirs as src/tests/docs/config/etc."""
        mapping: dict[str, str] = {}
        for entry in sorted(root.iterdir()):
            if not entry.is_dir() or entry.name in _SKIP_DIRS:
                continue
            name = entry.name.lower()
            if name in {"src", "source", "lib", "app", "core", "pkg"}:
                kind = "source"
            elif name in {"tests", "test", "spec", "e2e", "__tests__"}:
                kind = "tests"
            elif name in {"docs", "doc", "documentation", "wiki"}:
                kind = "docs"
            elif name in {"config", "conf", "cfg", "settings"}:
                kind = "config"
            elif name in {"scripts", "bin", "tools", "util", "utils"}:
                kind = "tooling"
            elif name in {"assets", "static", "public", "media", "images"}:
                kind = "assets"
            elif name in {".github", ".gitlab", ".circleci"}:
                kind = "ci"
            elif name in {"data", "datasets", "fixtures"}:
                kind = "data"
            elif name in {"examples", "samples", "demo", "demos"}:
                kind = "examples"
            else:
                kind = "other"
            mapping[entry.name] = kind
        return mapping

    def _detect_tech_stack(self, structure: StructureStats) -> dict[str, int]:
        """Languages by extension distribution (file counts)."""
        stack: Counter[str] = Counter()
        for ext, count in structure.files_by_ext.items():
            lang = _LANG_BY_EXT.get(ext)
            if lang:
                stack[lang] += count
        return dict(stack.most_common(10))

    # ── file iteration ────────────────────────────────────────────────

    def _iter_files(self, root: Path, exts: set[str]):
        """Yield files under root with given extensions, skipping junk dirs."""
        count = 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fname in filenames:
                if Path(fname).suffix.lower() in exts:
                    yield Path(dirpath) / fname
                    count += 1
                    if count >= self.max_files:
                        return


# ── Smoke test ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    project_root = Path(__file__).resolve().parent.parent
    print(f"Analyzing {project_root} ...\n")
    pi = ProjectIntelligence()
    report = pi.analyze_project(project_root)
    print(report.to_markdown())
    # Also verify JSON round-trip
    data = report.to_dict()
    print(f"\n[to_dict] keys: {sorted(data.keys())}")
    print(
        f"[summary] {len(report.frameworks)} frameworks, "
        f"{len(report.patterns)} pattern hits, "
        f"{report.structure.file_count} files, "
        f"{report.structure.total_loc:,} LOC, "
        f"{len(report.recommendations)} recommendations"
    )
