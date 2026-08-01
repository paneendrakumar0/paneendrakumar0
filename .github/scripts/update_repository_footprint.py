from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from urllib.parse import quote


SOURCE_EXTENSIONS = {
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".ino",
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".cs", ".go", ".rs",
    ".rb", ".php", ".swift", ".kt", ".kts", ".dart", ".scala", ".sh",
    ".ps1", ".html", ".htm", ".css", ".scss", ".sass", ".less", ".sql",
    ".r", ".lua", ".m", ".mm", ".vue", ".svelte", ".yaml", ".yml",
    ".toml", ".xml",
}
SPARSE_PATTERNS = sorted(f"*{extension}" for extension in SOURCE_EXTENSIONS) + ["*.ipynb"]
EXCLUDED_PARTS = {
    "node_modules", "vendor", "vendors", "third_party", "third-party", "build",
    "dist", "out", "target", "coverage", "generated", "__pycache__", ".venv",
    "venv", "env", "packages", "obj", "bin", ".ipynb_checkpoints",
}
START = "<!--START_SECTION:repository-footprint-->"
END = "<!--END_SECTION:repository-footprint-->"


def api_repositories(username: str, token: str) -> list[dict]:
    request = urllib.request.Request(
        f"https://api.github.com/users/{quote(username)}/repos?per_page=100&type=owner",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "profile-repository-footprint",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def run(*arguments: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        arguments,
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout


def excluded(path: Path) -> bool:
    lowered = {part.lower() for part in path.parts}
    return bool(lowered & EXCLUDED_PARTS) or path.name.endswith((".min.js", ".min.css"))


def physical_lines(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
    except OSError:
        return 0


def notebook_lines(path: Path) -> int:
    try:
        notebook = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    return sum(
        len("".join(cell.get("source", [])).splitlines())
        for cell in notebook.get("cells", [])
        if cell.get("cell_type") == "code"
    )


def repository_lines(repository: dict, root: Path) -> tuple[int, int]:
    destination = root / repository["name"]
    run(
        "git", "clone", "--quiet", "--depth", "1", "--filter=blob:none",
        "--no-checkout", repository["clone_url"], str(destination),
    )
    run("git", "sparse-checkout", "set", "--no-cone", *SPARSE_PATTERNS, cwd=destination)
    run("git", "checkout", "--quiet", cwd=destination)

    tracked = run("git", "ls-files", "-z", cwd=destination).split("\0")
    files = 0
    lines = 0
    for relative in filter(None, tracked):
        relative_path = Path(relative)
        path = destination / relative_path
        if excluded(relative_path) or not path.is_file():
            continue
        if path.suffix.lower() == ".ipynb":
            lines += notebook_lines(path)
            files += 1
        elif path.suffix.lower() in SOURCE_EXTENSIONS:
            lines += physical_lines(path)
            files += 1
    return files, lines


def main() -> None:
    username = os.environ["GITHUB_USER"]
    token = os.environ["GH_TOKEN"]
    repositories = api_repositories(username, token)
    storage_kib = sum(repository.get("size", 0) for repository in repositories)
    owned = [
        repository
        for repository in repositories
        if not repository.get("fork") and repository.get("size", 0) > 0
    ]

    source_files = 0
    source_lines = 0
    with tempfile.TemporaryDirectory(prefix="profile-footprint-") as temporary:
        root = Path(temporary)
        for repository in owned:
            files, lines = repository_lines(repository, root)
            source_files += files
            source_lines += lines

    storage_mb = storage_kib / 1024
    storage_badge = quote(f"{storage_mb:,.1f} MB", safe="")
    lines_badge = quote(f"{source_lines:,}", safe="")
    section = f"""{START}
![Public Repository Storage](https://img.shields.io/badge/Public%20Repo%20Storage-{storage_badge}-2196F3?style=flat)
![Source Lines](https://img.shields.io/badge/Source%20Lines-{lines_badge}-2196F3?style=flat)

> 📦 **{storage_mb:,.1f} MB** across {len(repositories)} public repositories
>
> 🧮 **{source_lines:,} source lines** across {len(owned)} owned, non-fork repositories
>
> 📄 **{source_files:,} tracked source files** scanned
{END}"""

    readme_path = Path("README.md")
    readme = readme_path.read_text(encoding="utf-8")
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.DOTALL)
    if not pattern.search(readme):
        raise RuntimeError("Repository footprint markers are missing from README.md")
    readme_path.write_text(pattern.sub(section, readme), encoding="utf-8")


if __name__ == "__main__":
    main()
