"""Contract tests for the plugin manifest and hook registration.

These read the two JSON files as the runtime would: a wrong matcher or a
missing `|| true` disables a feature (or blocks a turn) silently, so the
shape is asserted rather than assumed.
"""
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _hooks():
    return json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]


def _entries():
    for event, entries in _hooks().items():
        for entry in entries:
            for hook in entry["hooks"]:
                yield event, hook


def test_plugin_manifest_fields():
    m = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    for field in ("name", "version", "description", "author", "license"):
        assert m.get(field), f"нема поля {field}"
    assert m["name"] == "sincelast"


def test_only_three_events_registered():
    assert set(_hooks()) == {"SessionStart", "Stop", "UserPromptSubmit"}


def test_every_entry_has_explicit_timeout():
    for event, hook in _entries():
        assert "timeout" in hook, f"{event}: нема явного timeout"
        assert isinstance(hook["timeout"], int) and hook["timeout"] > 0


def test_fail_open_suffix_present():
    for event, hook in _entries():
        assert hook["command"].rstrip().endswith("|| true"), \
            f"{event}: нема '|| true' — синтаксична помилка стане блокуванням"


def test_uses_bash_shell():
    for event, hook in _entries():
        assert hook.get("shell") == "bash", f"{event}: не bash"


def test_points_at_single_file_entrypoint():
    for event, hook in _entries():
        assert "hooks/sincelast.py" in hook["command"], f"{event}: не той entrypoint"
        assert "${CLAUDE_PLUGIN_ROOT}" in hook["command"], f"{event}: шлях не від кореня плагіна"


def test_entrypoint_exists():
    assert (ROOT / "hooks" / "sincelast.py").is_file()


def test_f5_session_start_matcher_covers_exactly_five_sources():
    """F5: матчер SessionStart — регулярний вираз, що збігається з
    кожним із п'яти документованих source і ні з чим іншим."""
    matcher = _hooks()["SessionStart"][0]["matcher"]
    pattern = re.compile(matcher)
    for good in ("startup", "resume", "clear", "fork", "compact"):
        assert pattern.fullmatch(good), f"матчер не збігається з {good!r}"
    for bad in ("startupx", "STARTUP", "", "resume ", " resume", "foo", "startup|resume"):
        assert not pattern.fullmatch(bad), f"матчер хибно збігається з {bad!r}"
