"""Tests that credentials cannot leak, and that the repo stays publishable.

Every value in this file is fabricated. None of them is a real key, and the
patterns are chosen to look like real ones so the redaction is actually
exercised.

The point of these tests is that this project is going to be a public
repository. A leak is not a bug you fix in the next release — it is a key you
have to revoke.
"""

import re
import subprocess
import unittest
from pathlib import Path

from src.config import MissingCredential, redact, require_api_key

ROOT = Path(__file__).resolve().parents[1]

# Fabricated, correctly-shaped, never real.
FAKE_ANTHROPIC = "sk-ant-api03-" + "A" * 40
FAKE_OPENAI = "sk-proj-" + "B" * 40
FAKE_AWS = "AKIA" + "C" * 16
FAKE_GITHUB = "ghp_" + "D" * 36


class Redaction(unittest.TestCase):
    def assert_scrubbed(self, secret: str, text: str):
        cleaned = redact(text)
        self.assertNotIn(secret, cleaned, f"redact() let a secret through: {text[:30]}")

    def test_anthropic_key_is_removed(self):
        self.assert_scrubbed(FAKE_ANTHROPIC, f"auth failed for {FAKE_ANTHROPIC}")

    def test_openai_key_is_removed(self):
        self.assert_scrubbed(FAKE_OPENAI, f"rejected key {FAKE_OPENAI}")

    def test_aws_key_is_removed(self):
        self.assert_scrubbed(FAKE_AWS, f"using {FAKE_AWS} for upload")

    def test_github_token_is_removed(self):
        self.assert_scrubbed(FAKE_GITHUB, f"token {FAKE_GITHUB} invalid")

    def test_bearer_header_is_removed(self):
        self.assert_scrubbed("E" * 40, "Authorization: Bearer " + "E" * 40)

    def test_database_password_is_removed_but_host_is_kept(self):
        url = "postgresql://postgres:hunter2secret@localhost:5432/supply_chain"
        cleaned = redact(url)
        self.assertNotIn("hunter2secret", cleaned)
        self.assertIn("localhost:5432", cleaned)

    def test_named_key_assignment_is_removed(self):
        """Underscores in a name must not defeat the pattern."""
        for name in (
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "api_key",
            "SECRET",
            "db_password",
        ):
            with self.subTest(name=name):
                self.assert_scrubbed("Z" * 30, f"{name}={'Z' * 30}")

    def test_ordinary_text_is_left_alone(self):
        """Over-redacting everything would make errors useless."""
        text = "Projected shortage is 1800 units of CHK-250 at BER on 2026-09-29"
        self.assertEqual(redact(text), text)

    def test_redaction_accepts_non_strings(self):
        """Exceptions get passed in directly, not as strings."""
        self.assertIn("1800", redact(ValueError("shortage 1800")))


class CredentialErrors(unittest.TestCase):
    def test_missing_key_raises_with_instructions(self):
        import os

        saved = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            with self.assertRaises(MissingCredential) as caught:
                require_api_key("ANTHROPIC_API_KEY")
            message = str(caught.exception)
            self.assertIn(".env", message)
            self.assertIn("ANTHROPIC_API_KEY", message)
        finally:
            if saved is not None:
                os.environ["ANTHROPIC_API_KEY"] = saved

    def test_the_error_does_not_contain_a_value(self):
        import os

        saved = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = FAKE_OPENAI
        try:
            # A present key must not be echoed back by the accessor's own path.
            returned = require_api_key("OPENAI_API_KEY")
            self.assertEqual(returned, FAKE_OPENAI)
        finally:
            if saved is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = saved


class RepositoryHygiene(unittest.TestCase):
    """Guards against the ways a key reaches a public repository."""

    def test_gitignore_excludes_the_env_file(self):
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".env", text)

    def test_gitignore_keeps_the_example_file(self):
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("!.env.example", text)

    def test_gitignore_excludes_database_and_log_artifacts(self):
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for pattern in ("pgdata/", "*.log", ".venv/", "__pycache__/"):
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, text)

    def test_env_file_is_not_tracked_by_git(self):
        """.gitignore does not untrack a file git already knows about."""
        result = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            self.skipTest("not a git repository")
        tracked = [Path(p).name for p in result.stdout.splitlines()]
        self.assertNotIn(".env", tracked)

    def test_env_example_holds_placeholders_not_real_keys(self):
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        for pattern in (
            r"sk-ant-[A-Za-z0-9_-]{20,}",
            r"sk-(?:proj-)?[A-Za-z0-9_-]{32,}",
        ):
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, text))

    def test_no_source_file_hardcodes_a_provider_key(self):
        key_shapes = re.compile(
            r"sk-ant-[A-Za-z0-9_-]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{32,}|AKIA[0-9A-Z]{16}"
        )
        checked = 0
        for path in list((ROOT / "src").rglob("*.py")) + list(
            (ROOT / "scripts").rglob("*.py")
        ):
            checked += 1
            with self.subTest(path=path.name):
                self.assertIsNone(key_shapes.search(path.read_text(encoding="utf-8")))
        self.assertGreater(checked, 0)

    def test_the_secrets_scanner_passes(self):
        """The scanner that gates publication must pass on this repo."""
        result = subprocess.run(
            ["python3", "scripts/check_secrets.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout[-2000:])


class SyntheticDataOnly(unittest.TestCase):
    """The published data must stay synthetic and free of real detail."""

    def test_policy_documents_declare_themselves_synthetic(self):
        docs = list((ROOT / "data" / "rag_docs").glob("*.md"))
        self.assertGreater(len(docs), 0)
        for path in docs:
            with self.subTest(doc=path.name):
                text = path.read_text(encoding="utf-8").lower()
                self.assertIn("synthetic", text)

    def test_no_real_email_addresses_in_data_or_docs(self):
        email = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
        targets = (
            list((ROOT / "data").rglob("*"))
            + list(ROOT.glob("*.md"))
        )
        for path in targets:
            if not path.is_file():
                continue
            with self.subTest(path=path.name):
                self.assertIsNone(email.search(path.read_text(encoding="utf-8")))

    def test_no_employer_name_in_the_repository(self):
        """Removed during review; this keeps it from coming back."""
        targets = (
            list(ROOT.glob("*.md"))
            + list((ROOT / "data").rglob("*"))
            + list((ROOT / "src").rglob("*.py"))
        )
        for path in targets:
            if not path.is_file():
                continue
            with self.subTest(path=path.name):
                self.assertNotIn(
                    "hellofresh", path.read_text(encoding="utf-8").lower()
                )


if __name__ == "__main__":
    unittest.main()
