"""
Tests for `.env` loading.

Two behaviours here are load-bearing rather than cosmetic:

- **The environment outranks the file.** A `.env` that could override an
  explicitly-set variable would let a stray file in a working directory redirect
  a production run's database path, or switch on a paid model provider without
  anyone asking for it.
- **Secrets are never rendered.** The summary helper exists so a startup line can
  confirm configuration was picked up without a key landing in a terminal
  scrollback, a log aggregator, or a screen recording.
"""
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from mini_platform.config import (
    describe_loaded,
    load_env_file,
    parse_env_file,
)


class EnvFileTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.path = Path(self._tmp.name) / ".env"
        self._saved = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)
        self._tmp.cleanup()

    def write(self, text: str) -> Path:
        self.path.write_text(text, encoding="utf-8")
        return self.path


class TestParsing(EnvFileTestCase):
    def test_quoted_and_bare_values(self):
        parsed = parse_env_file(self.write(
            'DOUBLE="one"\n'
            "SINGLE='two'\n"
            "BARE=three\n"
        ))
        self.assertEqual(parsed, {"DOUBLE": "one", "SINGLE": "two", "BARE": "three"})

    def test_comments_and_blank_lines_are_ignored(self):
        parsed = parse_env_file(self.write(
            "# a comment\n"
            "\n"
            "   \n"
            "KEPT=yes\n"
            "# LLM_PROVIDER=\"gemini\"\n"
        ))
        self.assertEqual(parsed, {"KEPT": "yes"})

    def test_inline_comment_stripped_only_from_bare_values(self):
        """A `#` inside quotes is data; outside them it starts a comment."""
        parsed = parse_env_file(self.write(
            "BARE=value   # trailing note\n"
            'QUOTED="a#b"\n'
        ))
        self.assertEqual(parsed["BARE"], "value")
        self.assertEqual(parsed["QUOTED"], "a#b")

    def test_export_prefix_is_tolerated(self):
        parsed = parse_env_file(self.write('export SHELL_STYLE="ok"\n'))
        self.assertEqual(parsed, {"SHELL_STYLE": "ok"})

    def test_malformed_lines_do_not_raise(self):
        """A typo in a config file must not make the platform unstartable."""
        parsed = parse_env_file(self.write(
            "this line has no equals sign\n"
            "=novalue\n"
            "BAD NAME=x\n"
            "GOOD=y\n"
        ))
        self.assertEqual(parsed, {"GOOD": "y"})

    def test_missing_file_is_empty_not_an_error(self):
        self.assertEqual(parse_env_file(Path(self._tmp.name) / "absent"), {})


class TestPrecedence(EnvFileTestCase):
    def test_existing_environment_wins(self):
        os.environ["ALREADY_SET"] = "from-environment"
        applied = load_env_file(self.write('ALREADY_SET="from-file"\n'))
        self.assertEqual(os.environ["ALREADY_SET"], "from-environment")
        self.assertNotIn("ALREADY_SET", applied)

    def test_unset_variables_are_applied(self):
        os.environ.pop("NOT_YET_SET", None)
        applied = load_env_file(self.write('NOT_YET_SET="from-file"\n'))
        self.assertEqual(os.environ["NOT_YET_SET"], "from-file")
        self.assertEqual(applied, {"NOT_YET_SET": "from-file"})

    def test_override_is_opt_in(self):
        os.environ["OVERRIDABLE"] = "original"
        load_env_file(self.write('OVERRIDABLE="replaced"\n'), override=True)
        self.assertEqual(os.environ["OVERRIDABLE"], "replaced")

    def test_empty_env_file_variable_disables_loading(self):
        """How the test suite keeps itself off a developer's live provider."""
        os.environ["ENV_FILE"] = ""
        os.environ.pop("SHOULD_NOT_APPEAR", None)
        self.write('SHOULD_NOT_APPEAR="x"\n')
        self.assertEqual(load_env_file(), {})
        self.assertNotIn("SHOULD_NOT_APPEAR", os.environ)

    def test_env_file_variable_selects_the_path(self):
        os.environ["ENV_FILE"] = str(self.write('CHOSEN="yes"\n'))
        os.environ.pop("CHOSEN", None)
        self.assertEqual(load_env_file(), {"CHOSEN": "yes"})


class TestSecretRendering(EnvFileTestCase):
    def test_secret_like_names_are_masked(self):
        summary = describe_loaded({
            "GEMINI_API_KEY": "AQ.super-secret-value",
            "GROQ_API_KEY": "gsk-secret",
            "APPROVAL_SIGNING_SECRET": "hmac-secret",
            "SOME_TOKEN": "tok",
            "DB_PASSWORD": "pw",
            "AWS_CREDENTIAL": "cred",
        })
        for secret in ("AQ.super-secret-value", "gsk-secret", "hmac-secret", "tok", "pw", "cred"):
            self.assertNotIn(secret, summary)
        self.assertIn("GEMINI_API_KEY=<set>", summary)

    def test_ordinary_values_are_shown(self):
        summary = describe_loaded({"LLM_PROVIDER": "gemini", "LLM_MODEL": "gemini-3.5-flash"})
        self.assertIn("LLM_PROVIDER=gemini", summary)
        self.assertIn("LLM_MODEL=gemini-3.5-flash", summary)

    def test_nothing_loaded_is_stated_plainly(self):
        self.assertEqual(describe_loaded({}), "no .env values applied")


if __name__ == "__main__":
    unittest.main()
