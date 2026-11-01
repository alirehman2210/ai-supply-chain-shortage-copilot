"""Scans the repository and its full git history for credentials.

Run this before publishing, and before any push:

    python3 scripts/check_secrets.py

It never prints a matched value. For each finding it reports the file, the line
number, and which rule matched, so a real secret is not copied into terminal
output, a screenshot, or an AI conversation on its way to being reported.

Exit code 0 means nothing was found. Exit code 1 means something needs a human
to look at it.

What it checks:
  1. Provider key formats in every file a push would publish — tracked files
     plus untracked ones git does not ignore (OpenAI, Anthropic, AWS, GitHub,
     Slack, Google, private keys). A local .env is deliberately out of scope,
     since that is where keys are supposed to live; check 3b proves it is
     ignored instead.
  2. The same patterns across every commit in git history
  3. That .env is not tracked by git, whatever .gitignore says
  4. That .gitignore actually covers the secret files
  5. That .env.example contains placeholders rather than real values

This is a pattern scan, not a guarantee. It finds keys that look like keys. A
credential in an unusual format can still slip past it.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Each rule is (name, compiled pattern). Patterns are written to match the
# shape of a real credential and not the placeholder text in .env.example.
RULES: list[tuple[str, re.Pattern]] = [
    # The negative lookahead keeps an Anthropic key from also matching here
    # and being reported twice under the wrong name.
    ("OpenAI API key", re.compile(r"sk-(?!ant-)(?:proj-)?[A-Za-z0-9_-]{32,}")),
    ("Anthropic API key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("AWS access key id", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("Slack token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_-]{30,}")),
    ("Private key block", re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")),
    ("Generic bearer secret", re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-.]{30,}")),
]

# Directories never worth scanning: not ours, or enormous.
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "htmlcov",
}

# This file necessarily contains the patterns themselves.
SELF = Path(__file__).name


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return result.stdout


def publishable_files() -> list[Path]:
    """Files a push would actually publish.

    Tracked files plus untracked ones that .gitignore does not exclude. This is
    the surface that matters: a key sitting in .env is exactly where it belongs
    and is never published, so scanning it only produces a finding that has to
    be explained away every time. That .env really is ignored is checked
    separately, by check_env_not_tracked() and check_gitignore().
    """
    listing = git("ls-files", "--cached", "--others", "--exclude-standard")
    paths = []
    for line in listing.splitlines():
        if not line.strip():
            continue
        path = ROOT / line
        if path.is_file() and not any(part in SKIP_DIRS for part in path.parts):
            paths.append(path)
    return paths


def scan_working_tree() -> list[str]:
    findings = []
    candidates = publishable_files()
    if not candidates:
        # Not a git repository, so fall back to walking the directory. Without
        # git there is no ignore list to trust, so .env is skipped by name.
        candidates = [
            path
            for path in ROOT.rglob("*")
            if path.is_file()
            and not any(part in SKIP_DIRS for part in path.parts)
            and path.name != ".env"
            and not path.name.startswith(".env.")
        ]

    for path in candidates:
        if path.name == SELF:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable; nothing to match on

        relative = path.relative_to(ROOT)
        for line_number, line in enumerate(text.splitlines(), start=1):
            for name, pattern in RULES:
                if pattern.search(line):
                    findings.append(f"{relative}:{line_number} — {name}")
    return findings


def check_local_env_is_ignored() -> list[str]:
    """If a local .env exists, it must be ignored. Reported, never read out."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return []
    result = subprocess.run(
        ["git", "check-ignore", "-q", ".env"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return [
            ".env exists and git does NOT ignore it. Anything in it would be "
            "published. Add .env to .gitignore before doing anything else."
        ]
    return []


def scan_history() -> list[str]:
    """Scan every commit, not just the current checkout.

    A secret removed in a later commit is still in the history and still
    published when the repository goes public.
    """
    commits = git("rev-list", "--all").split()
    if not commits:
        return []

    findings = []
    for name, pattern in RULES:
        output = git("grep", "-nIE", pattern.pattern, *commits)
        for line in output.splitlines():
            # Format is <commit>:<path>:<lineno>:<content>. Keep the location,
            # drop the content so no secret value is ever printed.
            parts = line.split(":", 3)
            if len(parts) >= 3:
                findings.append(
                    f"commit {parts[0][:9]} — {parts[1]}:{parts[2]} — {name}"
                )
    return findings


def check_env_not_tracked() -> list[str]:
    tracked = set(git("ls-files").splitlines())
    problems = []
    for candidate in tracked:
        base = Path(candidate).name
        if base == ".env" or (base.startswith(".env.") and base != ".env.example"):
            problems.append(
                f"{candidate} is TRACKED by git. .gitignore does not untrack a file "
                f"git already knows about — run: git rm --cached {candidate}"
            )
    return problems


def check_gitignore() -> list[str]:
    path = ROOT / ".gitignore"
    if not path.exists():
        return ["There is no .gitignore."]
    text = path.read_text(encoding="utf-8")
    problems = []
    for needed in (".env", "pgdata/", "__pycache__/", ".venv/"):
        if needed not in text:
            problems.append(f".gitignore does not mention {needed}")
    return problems


def check_example_has_placeholders() -> list[str]:
    path = ROOT / ".env.example"
    if not path.exists():
        return [".env.example is missing, so there is no template to copy."]
    problems = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        for name, pattern in RULES:
            if pattern.search(line):
                problems.append(
                    f".env.example:{line_number} looks like a real {name}. "
                    f"It must hold placeholders only."
                )
    return problems


def report(title: str, findings: list[str]) -> bool:
    print(f"\n{title}")
    if not findings:
        print("  clean")
        return True
    for finding in findings:
        print(f"  FOUND  {finding}")
    return False


def main() -> int:
    print("Secret scan —", ROOT)
    print("Matched values are never printed.")

    results = [
        report("1. Files git would publish", scan_working_tree()),
        report("2. Git history (all commits)", scan_history()),
        report("3. .env tracking", check_env_not_tracked()),
        report("3b. local .env is ignored", check_local_env_is_ignored()),
        report("4. .gitignore coverage", check_gitignore()),
        report("5. .env.example placeholders", check_example_has_placeholders()),
    ]

    print()
    if all(results):
        print("PASS — no credentials found. Safe to publish on this check.")
        print("This is a pattern scan, not a proof. Review the diff before pushing.")
        return 0

    print("FAIL — review the findings above before publishing.")
    print("If a real credential was committed, revoke and rotate it first.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
