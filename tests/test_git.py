import pathlib, subprocess, sys
import pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "hooks"))
import sincelast as sl


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "r"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    _git(r, "commit", "-q", "--allow-empty", "-m", "c1")
    return r


def test_snapshot_shape(repo):
    snap = sl.git_snapshot(str(repo), 2.0)
    assert snap["branch"] == "main"
    assert len(snap["head"]) == 40
    assert snap["toplevel"].endswith("/r")


def test_not_a_repo_returns_none(tmp_path):
    assert sl.git_snapshot(str(tmp_path), 2.0) is None


def test_no_change_is_silent(repo):
    snap = sl.git_snapshot(str(repo), 2.0)
    assert sl.git_compare(snap, snap, str(repo), 2.0) is None


def test_none_old_is_silent(repo):
    assert sl.git_compare(None, sl.git_snapshot(str(repo), 2.0), str(repo), 2.0) is None


def test_different_toplevel_is_silent(repo):
    other = dict(sl.git_snapshot(str(repo), 2.0))
    other["toplevel"] = "/some/other/repo"
    assert sl.git_compare(other, sl.git_snapshot(str(repo), 2.0), str(repo), 2.0) is None


def test_fast_forward_is_ahead_with_count(repo):
    old = sl.git_snapshot(str(repo), 2.0)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "c2")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "c3")
    kind, params = sl.git_compare(old, sl.git_snapshot(str(repo), 2.0), str(repo), 2.0)
    assert kind == "AHEAD"
    assert params["n"] == 2
    assert params["branch"] == "main"


def test_rebase_same_branch_is_diverged(repo):
    """F2 контроль: rebase на тій самій гілці — DIVERGED, не AHEAD.

    Базис береться ПІСЛЯ c2: перепис має зсунути саме той коміт, який
    агент бачив. Базис до c2 лишився б предком переписаного HEAD і дав
    би AHEAD за побудовою, не перевіривши нічого."""
    _git(repo, "commit", "-q", "--allow-empty", "-m", "c2")
    old = sl.git_snapshot(str(repo), 2.0)
    _git(repo, "commit", "--amend", "--allow-empty", "-q", "-m", "c2-amended")
    kind, params = sl.git_compare(old, sl.git_snapshot(str(repo), 2.0), str(repo), 2.0)
    assert kind == "DIVERGED"
    assert params["branch"] == "main"


def test_branch_switch_is_moved_not_ahead(repo):
    """F2: перемикання гілки — MOVED, ніколи AHEAD/DIVERGED навіть
    якщо нова гілка попереду за комітами."""
    old = sl.git_snapshot(str(repo), 2.0)
    _git(repo, "checkout", "-q", "-b", "feature")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "c2")
    kind, params = sl.git_compare(old, sl.git_snapshot(str(repo), 2.0), str(repo), 2.0)
    assert kind == "MOVED"
    assert "old" in params and "new" in params


def test_checkout_older_commit_is_moved(repo):
    """F2: git checkout HEAD~N — переміщення, не перепис."""
    _git(repo, "commit", "-q", "--allow-empty", "-m", "c2")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "c3")
    old = sl.git_snapshot(str(repo), 2.0)
    _git(repo, "checkout", "-q", "HEAD~2")
    kind, _ = sl.git_compare(old, sl.git_snapshot(str(repo), 2.0), str(repo), 2.0)
    assert kind == "MOVED"


def test_detach_is_moved(repo):
    old = sl.git_snapshot(str(repo), 2.0)
    _git(repo, "checkout", "-q", "--detach")
    new = sl.git_snapshot(str(repo), 2.0)
    assert new["branch"] is None
    kind, _ = sl.git_compare(old, new, str(repo), 2.0)
    assert kind == "MOVED"


def test_switch_to_divergent_branch_is_moved(repo):
    """F2: git switch на розбіжну гілку — MOVED, без ancestor/лічильника."""
    _git(repo, "checkout", "-q", "-b", "feature")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "feature-c2")
    old = sl.git_snapshot(str(repo), 2.0)
    _git(repo, "switch", "-q", "main")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "main-c2")
    kind, _ = sl.git_compare(old, sl.git_snapshot(str(repo), 2.0), str(repo), 2.0)
    assert kind == "MOVED"


def test_env_hardening(monkeypatch, repo):
    """LC_ALL=C обов'язковий: локалізований вивід git ламає парсинг."""
    seen = {}
    real = sl.subprocess.run
    def spy(cmd, **kw):
        seen.update(kw.get("env") or {})
        return real(cmd, **kw)
    monkeypatch.setattr(sl.subprocess, "run", spy)
    sl.git_snapshot(str(repo), 2.0)
    assert seen.get("LC_ALL") == "C"


def test_ancestor_helper_three_states(repo):
    a = _git(repo, "rev-parse", "HEAD")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "c2")
    b = _git(repo, "rev-parse", "HEAD")
    assert sl._is_ancestor(str(repo), a, b, 2.0) is True
    assert sl._is_ancestor(str(repo), b, a, 2.0) is False
    assert sl._is_ancestor(str(repo), "0" * 40, b, 2.0) is None
