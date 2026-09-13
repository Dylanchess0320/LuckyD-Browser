"""Project Intelligence Engine — auto-detect project type, framework, conventions."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .types import ProjectInfo


class ProjectDetector:
    """Detects project type and gathers key context."""

    FRAMEWORK_SIGS = {
        "Python": [
            ("django", "Django"),
            ("flask", "Flask"),
            ("fastapi", "FastAPI"),
            ("tornado", "Tornado"),
            ("starlette", "Starlette"),
            ("aiohttp", "aiohttp"),
            ("pytest", "pytest"),
            ("sqlalchemy", "SQLAlchemy"),
            ("numpy", "NumPy"),
            ("pandas", "Pandas"),
            ("torch", "PyTorch"),
            ("tensorflow", "TensorFlow"),
        ],
        "JavaScript": [
            ("react", "React"),
            ("vue", "Vue.js"),
            ("angular", "Angular"),
            ("svelte", "Svelte"),
            ("next", "Next.js"),
            ("nuxt", "Nuxt.js"),
            ("express", "Express.js"),
            ("nestjs", "NestJS"),
            ("jest", "Jest"),
        ],
        "TypeScript": [
            ("react", "React"),
            ("vue", "Vue.js"),
            ("angular", "Angular"),
            ("next", "Next.js"),
            ("nuxt", "Nuxt.js"),
            ("express", "Express.js"),
            ("nestjs", "NestJS"),
            ("typeorm", "TypeORM"),
            ("prisma", "Prisma"),
        ],
        "Rust": [
            ("axum", "Axum"),
            ("actix", "Actix-web"),
            ("rocket", "Rocket"),
            ("tokio", "Tokio"),
            ("serde", "Serde"),
        ],
        "Go": [
            ("gin", "Gin"),
            ("echo", "Echo"),
            ("fiber", "Fiber"),
            ("cobra", "Cobra"),
            ("gorm", "GORM"),
        ],
    }

    BUILD_SYSTEMS = {
        "pyproject.toml": "poetry/pdm",
        "setup.py": "setuptools",
        "setup.cfg": "setuptools",
        "requirements.txt": "pip",
        "Pipfile": "pipenv",
        "Cargo.toml": "cargo",
        "go.mod": "go mod",
        "package.json": "npm",
        "yarn.lock": "yarn",
        "pnpm-lock.yaml": "pnpm",
        "Gemfile": "bundler",
        "composer.json": "composer",
        "build.gradle": "gradle",
        "build.gradle.kts": "gradle",
        "pom.xml": "maven",
        "Makefile": "make",
        "CMakeLists.txt": "cmake",
        "mix.exs": "mix",
    }

    def detect(self, path: str | Path) -> ProjectInfo:
        """Detect project information from a directory."""
        root = Path(path).resolve()
        if not root.exists():
            return ProjectInfo(root=str(root))

        info = ProjectInfo(root=str(root))
        info.name = root.name

        # Walk files
        dirs_walked = 0
        files_walked = 0
        all_files = []

        for dirpath, dirnames, filenames in os.walk(str(root), topdown=True):
            dirnames[:] = [
                d
                for d in dirnames
                if not d.startswith(
                    (
                        ".",
                        "__",
                        "node_modules",
                        "venv",
                        ".venv",
                        ".git",
                        "target",
                        "build",
                        "dist",
                        ".next",
                        ".nuxt",
                        "__pycache__",
                    )
                )
            ]
            dirs_walked += 1
            for f in filenames:
                files_walked += 1
                fp = Path(dirpath) / f
                try:
                    all_files.append(str(fp.relative_to(root)))
                except ValueError:
                    continue
            if dirs_walked > 200 or files_walked > 2000:
                break

        info.total_files = files_walked
        info.total_dirs = dirs_walked

        info.language = self._detect_language(root, all_files)
        info.language_version = self._detect_lang_version(root, info.language)
        info.framework = self._detect_framework(root, info.language)
        info.build_system = self._detect_build_system(root)
        info.package_manager = self._detect_package_manager(root, info.language)
        info.test_framework = self._detect_test_framework(root, info.language)
        info.linter, info.formatter = self._detect_lint_format(root, info.language)
        info.key_files = self._find_key_files(root, info.language)
        info.entry_point = self._find_entry_point(root, info.language)
        info.has_tests = any("test" in f.lower() for f in all_files[:100])
        info.has_docs = (
            any(f.startswith("docs/") for f in all_files[:100]) or (root / "docs").is_dir()
        )
        info.has_docker = (root / "Dockerfile").exists() or (root / "docker-compose.yml").exists()
        info.has_ci = (
            any(f.startswith(".github/") for f in all_files[:50])
            or (root / ".github").is_dir()
            or (root / ".gitlab-ci.yml").exists()
        )
        info.has_readme = any(f.lower().startswith("readme") for f in os.listdir(root))

        return info

    def _detect_language(self, root: Path, files: list[str]) -> str:
        """Detect primary language from file extensions and manifest files."""
        ext_counts: dict[str, int] = {}
        for f in files:
            ext = Path(f).suffix.lower()
            if ext:
                ext_counts[ext] = ext_counts.get(ext, 0) + 1

        ext_map = {
            ".py": "Python",
            ".js": "JavaScript",
            ".jsx": "JavaScript",
            ".ts": "TypeScript",
            ".tsx": "TypeScript",
            ".go": "Go",
            ".rs": "Rust",
            ".java": "Java",
            ".rb": "Ruby",
            ".php": "PHP",
            ".c": "C",
            ".h": "C",
            ".cpp": "C++",
            ".hpp": "C++",
            ".cs": "C#",
            ".swift": "Swift",
            ".kt": "Kotlin",
            ".kts": "Kotlin",
        }

        lang_scores: dict[str, int] = {}
        for ext, count in ext_counts.items():
            lang = ext_map.get(ext)
            if lang:
                lang_scores[lang] = lang_scores.get(lang, 0) + count

        if (root / "Cargo.toml").exists():
            lang_scores["Rust"] = lang_scores.get("Rust", 0) + 100
        if (root / "go.mod").exists():
            lang_scores["Go"] = lang_scores.get("Go", 0) + 100
        if (root / "package.json").exists():
            if (root / "tsconfig.json").exists():
                lang_scores["TypeScript"] = lang_scores.get("TypeScript", 0) + 100
            else:
                lang_scores["JavaScript"] = lang_scores.get("JavaScript", 0) + 100
        if (root / "pyproject.toml").exists() or (root / "setup.py").exists():
            lang_scores["Python"] = lang_scores.get("Python", 0) + 50

        if not lang_scores:
            return ""
        return max(lang_scores, key=lang_scores.get)

    def _detect_framework(self, root: Path, language: str) -> str:
        if not language:
            return ""
        deps_content = ""
        if (root / "package.json").exists() and language in ("JavaScript", "TypeScript"):
            try:
                data = json.loads((root / "package.json").read_text())
                deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                deps_content = " ".join(deps.keys()).lower()
            except (json.JSONDecodeError, OSError):
                pass
        elif language == "Python":
            for fname in ["requirements.txt", "pyproject.toml"]:
                fp = root / fname
                if fp.exists():
                    deps_content += fp.read_text().lower()
        elif language == "Rust" and (root / "Cargo.toml").exists():
            deps_content = (root / "Cargo.toml").read_text().lower()
        elif language == "Go" and (root / "go.mod").exists():
            deps_content = (root / "go.mod").read_text().lower()

        for pattern, fw_name in self.FRAMEWORK_SIGS.get(language, []):
            if pattern in deps_content:
                return fw_name
        return ""

    def _detect_build_system(self, root: Path) -> str:
        for filename, build in self.BUILD_SYSTEMS.items():
            if (root / filename).exists():
                return build
        return ""

    def _detect_lang_version(self, root: Path, language: str) -> str:
        if language == "Python":
            pf = root / "pyproject.toml"
            if pf.exists():
                m = re.search(r'requires-python\s*=\s*["\']([^"\']+)', pf.read_text())
                if m:
                    return m.group(1)
        if language in ("JavaScript", "TypeScript"):
            pf = root / "package.json"
            if pf.exists():
                try:
                    eng = json.loads(pf.read_text()).get("engines", {})
                    return eng.get("node", "")
                except json.JSONDecodeError:
                    pass
        if language == "Rust":
            for fname in ["rust-toolchain.toml", "rust-toolchain"]:
                fp = root / fname
                if fp.exists():
                    return fp.read_text().strip()[:20]
        return ""

    def _detect_package_manager(self, root: Path, language: str) -> str:
        """Best-effort package manager from lockfiles / manifests."""
        if language in ("JavaScript", "TypeScript"):
            if (root / "pnpm-lock.yaml").exists():
                return "pnpm"
            if (root / "yarn.lock").exists():
                return "yarn"
            if (root / "package.json").exists():
                return "npm"
        elif language == "Python":
            if (root / "Pipfile").exists() or (root / "Pipfile.lock").exists():
                return "pipenv"
            if (root / "poetry.lock").exists():
                return "poetry"
            if (root / "requirements.txt").exists() or (root / "pyproject.toml").exists():
                return "pip"
        elif language == "Rust":
            if (root / "Cargo.toml").exists():
                return "cargo"
        elif language == "Go":
            if (root / "go.mod").exists():
                return "go mod"
        return ""

    def _detect_test_framework(self, root: Path, language: str) -> str:
        """Best-effort test runner from manifests / config files."""
        if language == "Python":
            content = ""
            for fname in ("requirements.txt", "pyproject.toml", "setup.cfg"):
                fp = root / fname
                if fp.exists():
                    try:
                        content += fp.read_text(encoding="utf-8", errors="replace").lower()
                    except OSError:
                        continue
            for name in ("pytest", "nose", "tox"):
                if name in content:
                    return name
            if (root / "pytest.ini").exists():
                return "pytest"
            return ""
        if language in ("JavaScript", "TypeScript"):
            pf = root / "package.json"
            if pf.exists():
                try:
                    data = json.loads(pf.read_text(encoding="utf-8"))
                    deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                    keys = " ".join(deps.keys()).lower()
                except (json.JSONDecodeError, OSError):
                    keys = ""
                for name in ("vitest", "jest", "mocha", "ava"):
                    if name in keys:
                        return name
            return ""
        if language == "Rust":
            return "cargo test"
        if language == "Go":
            return "go test"
        return ""

    def _detect_lint_format(self, root: Path, language: str) -> tuple[str, str]:
        """Return (linter, formatter) from config files present."""
        linter, formatter = "", ""
        if language == "Python":
            pyproject = ""
            pf = root / "pyproject.toml"
            if pf.exists():
                try:
                    pyproject = pf.read_text(encoding="utf-8", errors="replace").lower()
                except OSError:
                    pyproject = ""
            if (
                (root / ".ruff.toml").exists()
                or (root / "ruff.toml").exists()
                or "[tool.ruff]" in pyproject
            ):
                linter, formatter = "ruff", "ruff"
            elif (root / ".flake8").exists() or "flake8" in pyproject:
                linter = "flake8"
            if "[tool.black]" in pyproject:
                formatter = "black"
        elif language in ("JavaScript", "TypeScript"):
            if any(
                (root / f).exists()
                for f in (".eslintrc", ".eslintrc.json", ".eslintrc.js", "eslint.config.js")
            ):
                linter = "eslint"
            if any(
                (root / f).exists()
                for f in (".prettierrc", ".prettierrc.json", "prettier.config.js")
            ):
                formatter = "prettier"
        return linter, formatter

    _KEY_FILE_CANDIDATES = [
        "README.md",
        "readme.md",
        "pyproject.toml",
        "package.json",
        "requirements.txt",
        "Cargo.toml",
        "go.mod",
        "Makefile",
        "Dockerfile",
        "docker-compose.yml",
    ]

    def _find_key_files(self, root: Path, language: str) -> list[str]:
        """Well-known manifest / doc / build files that exist, for orientation."""
        return [name for name in self._KEY_FILE_CANDIDATES if (root / name).exists()][:12]

    _ENTRY_POINT_CANDIDATES: dict[str, list[str]] = {
        "Python": [
            "main.py",
            "app.py",
            "__main__.py",
            "manage.py",
            "wsgi.py",
            "src/main.py",
            "src/app.py",
        ],
        "JavaScript": ["index.js", "src/index.js", "main.js", "app.js"],
        "TypeScript": ["index.ts", "src/index.ts", "main.ts"],
        "Rust": ["src/main.rs"],
        "Go": ["main.go", "cmd/main.go"],
    }

    def _find_entry_point(self, root: Path, language: str) -> str:
        """Likely program entry point, or '' when nothing matches."""
        if language in ("JavaScript", "TypeScript"):
            pf = root / "package.json"
            if pf.exists():
                try:
                    main = json.loads(pf.read_text(encoding="utf-8")).get("main", "")
                    if main and (root / main).exists():
                        return main
                except (json.JSONDecodeError, OSError):
                    pass
        for name in self._ENTRY_POINT_CANDIDATES.get(language, []):
            if (root / name).exists():
                return name
        return ""
