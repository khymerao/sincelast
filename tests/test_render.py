import re, pathlib, sys
import pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "hooks"))
import sincelast as sl

# Заморожений whitelist слів, витягнутий зі шаблонів на момент їх
# написання. Тест не звіряє шаблони самі із собою в реальному часі —
# він фіксує словник тут, окремо, тож майбутня правка шаблону, що
# додає "please"/"should"/"verify", ламає тест, а не проходить мовчки.
WHITELIST = {
    "calendar", "date", "changed", "since", "this", "session", "started",
    "today", "is", "machine-local", "not", "git", "head", "on", "branch",
    "moved", "from", "to", "new", "commit(s)", "the", "agent's", "last",
    "turn", "ended", "an", "ancestor", "of", "position", "was", "now",
    "at", "detached",
}

TEMPLATES = [sl.T_DATE, sl.T_GIT_AHEAD, sl.T_GIT_DIVERGED, sl.T_GIT_MOVED,
             sl.P_BRANCH, sl.P_DETACHED]


def _words(text):
    """Слова шаблону. Дужки лишаються частиною слова, бо "commit(s)" —
    одне слово; але дужка, що тільки закриває обгортку ("(machine-local)"),
    словом не є і відрізається."""
    out = []
    for word in re.findall(r"[A-Za-z][\w'()-]*", text):
        if word.endswith(")") and "(" not in word:
            word = word[:-1]
        out.append(word)
    return out


@pytest.mark.parametrize("template", TEMPLATES)
def test_template_words_are_whitelisted(template):
    """F7: кожна константа T_*/P_* після вилучення плейсхолдерів ⊆ whitelist."""
    stripped = re.sub(r"\{[^}]*\}", "", template)
    words = _words(stripped)
    bad = [w for w in words if w.lower() not in WHITELIST]
    assert bad == [], f"слово поза whitelist у {template!r}: {bad}"


def test_render_date():
    out = sl.render_date("2026-09-12", "2026-09-11")
    assert out == ("Calendar date changed since this session started: "
                   "today is 2026-09-12 (machine-local), not 2026-09-11.")


def test_render_git_ahead():
    out = sl.render_git("AHEAD", {"branch": "main", "old": "a" * 40, "new": "b" * 40, "n": 3})
    assert out == ('Git HEAD on branch "main" moved from aaaaaaa to bbbbbbb: '
                   "3 new commit(s) since the agent's last turn ended.")


def test_render_git_diverged():
    out = sl.render_git("DIVERGED", {"branch": "main", "old": "a" * 40, "new": "c" * 40})
    assert out == ('Git HEAD on branch "main" moved from aaaaaaa to ccccccc; '
                   "aaaaaaa is not an ancestor of ccccccc.")


def test_render_git_moved_branch_switch():
    old = {"branch": "main", "head": "a" * 40}
    new = {"branch": "feature", "head": "a" * 40}
    out = sl.render_git("MOVED", {"old": old, "new": new})
    assert out == ('Git position changed since the agent\'s last turn ended: '
                   'was branch "main" at aaaaaaa, now branch "feature" at aaaaaaa.')
    assert "ancestor" not in out
    assert not re.search(r"\b\d+ new commit", out)


def test_render_git_moved_detached():
    old = {"branch": "main", "head": "a" * 40}
    new = {"branch": None, "head": "a" * 40}
    out = sl.render_git("MOVED", {"old": old, "new": new})
    assert "detached HEAD" in out


def test_render_git_unknown_kind_raises():
    with pytest.raises(ValueError):
        sl.render_git("BOGUS", {})


@pytest.mark.parametrize("branch", [
    'fix/checkout', 'наслідок', 'has "quotes" inside',
    "has\nnewline", "x" * 10000,
])
def test_render_survives_hostile_branch_names(branch):
    """F7: жодна з цих гілок не піднімає виняток, назва цитована."""
    old = {"branch": "main", "head": "a" * 40}
    new = {"branch": branch, "head": "b" * 40}
    out = sl.render_git("MOVED", {"old": old, "new": new})
    assert '"' in out


def test_date_fact_not_lost_alongside_hostile_git_fact():
    """F7: DATE у тому самому виводі не губиться, коли GIT-факт має
    ворожу назву гілки."""
    date_line = sl.render_date("2026-09-12", "2026-09-11")
    old = {"branch": "main", "head": "a" * 40}
    new = {"branch": "наслідок\nз лапками\"", "head": "b" * 40}
    git_line = sl.render_git("MOVED", {"old": old, "new": new})
    combined = "\n".join([date_line, git_line])
    assert "2026-09-12" in combined
    assert '"' in combined

def test_long_branch_is_truncated_with_ellipsis():
    """Spec 12b: truncation is marked, so a reader can tell a long name from
    a name that merely ends where the cap falls. Regression for a fix that
    originally shipped without one."""
    from sincelast import _sanitize_branch, _BRANCH_MAX_LEN
    out = _sanitize_branch("x" * (_BRANCH_MAX_LEN + 50))
    assert len(out) == _BRANCH_MAX_LEN
    assert out.endswith("\u2026")
    assert out[:-1] == "x" * (_BRANCH_MAX_LEN - 1)


def test_branch_at_exactly_the_cap_is_not_marked():
    """A name that fits is returned whole — no ellipsis, nothing removed."""
    from sincelast import _sanitize_branch, _BRANCH_MAX_LEN
    exact = "y" * _BRANCH_MAX_LEN
    assert _sanitize_branch(exact) == exact


# --- примітка про наслідок ------------------------------------------------

def test_stale_note_rides_on_a_git_fact():
    fact = sl.render_git("AHEAD", {"branch": "main", "old": "aaa", "new": "bbb", "n": 1})
    out = sl.with_stale(fact)
    assert out.startswith(fact)
    assert "may be stale" in out


def test_no_fact_means_no_note():
    assert sl.with_stale(None) is None
    assert sl.with_stale("") == ""


def test_stale_note_is_a_closed_constant():
    """Примітка теж константа, а не згенерований текст."""
    assert "{" not in sl.T_STALE


def test_stale_note_states_a_consequence_not_an_order():
    """Виміряно: наказ тягне зайві Read, констатація наслідку дає рівно
    одну перевірку. Формулювання не повертати в імператив."""
    low = sl.T_STALE.lower()
    for imperative in ("re-read", "reread", "check ", "verify", "make sure", "you should"):
        assert imperative not in low, f"імператив у примітці: {imperative!r}"
    assert "may be stale" in low
