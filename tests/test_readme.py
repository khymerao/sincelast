"""Contract tests for the distributed documentation.

The README is the only place a user sees before installing: what the
plugin reads, where it writes, how to purge it, how to turn it off. These
assertions pin the disclosures that must not silently drop out of it.
"""
import pathlib

README = (pathlib.Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")


def _first_prose_line() -> str:
    """Перше речення README — це перший непорожній рядок, що не є
    заголовком. Заголовок `# sincelast` не є реченням."""
    for line in README.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return ""


def test_first_sentence_matches_spec_verbatim():
    first = _first_prose_line()
    assert ("tells your Claude Code agent when the calendar date or "
            "the git HEAD moved behind its back") in first


def test_data_statement_present():
    for required in ["## Data", "does not leave the machine", "SINCELAST_DISABLE"]:
        assert required in README, f"нема: {required}"


def test_transcript_is_explicitly_disclaimed():
    """Найсильніша обіцянка плагіна — транскрипт не читається. Вона мусить
    бути в README, а не лише в докстрінгу."""
    assert "transcript_path" in README
    assert "never read" in README.lower()


def test_documents_python_requirement():
    """Стокова macOS без Xcode CLT відкриває GUI-діалог на python3."""
    assert "python3" in README.lower()
    assert "Command Line" in README or "xcode-select" in README


def test_documents_bash_requirement_for_windows():
    assert "bash" in README.lower()
    assert "Windows" in README


def test_bash_requirement_is_stated_as_mandatory():
    """Acceptance: сказано прямо, що bash обов'язковий — не натяком."""
    assert "bash is required" in README.lower()


def test_documents_devcontainer_utc_trap():
    assert "UTC" in README and ("TZ" in README or "localtime" in README)


def test_no_unverified_competitor_claim():
    for banned in ["crippled", "unlike other", "better than", "only plugin that"]:
        assert banned.lower() not in README.lower(), f"неперевірена теза: {banned}"


def test_documents_state_path():
    assert "XDG_STATE_HOME" in README


def test_documents_purge_and_retention():
    assert "rm -rf" in README
    assert "30 days" in README
