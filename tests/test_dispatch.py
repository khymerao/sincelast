import json, os, pathlib, subprocess, sys, tempfile, time
import pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "hooks"))
import sincelast as sl

ENTRY = pathlib.Path(__file__).resolve().parents[1] / "hooks" / "sincelast.py"


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path_factory):
    r = tmp_path_factory.mktemp("repo")
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    _git(r, "commit", "-q", "--allow-empty", "-m", "c1")
    return r


def base(event, session_id="s1", cwd=".", **extra):
    return dict({"hook_event_name": event, "session_id": session_id, "cwd": str(cwd),
                "transcript_path": "/should/never/be/opened"}, **extra)


# --- F3: DATE re-emit rules -------------------------------------------------

def test_f3_date_lifecycle(tmp_path, repo):
    root = tmp_path
    D1 = 1_800_000_000.0
    D2 = D1 + 86400          # календарний день пізніше

    out = sl.dispatch(base("SessionStart", cwd=repo, source="startup"), D1, root)
    assert out is None, "startup мусить бути тихим"

    out = sl.dispatch(base("SessionStart", cwd=repo, source="compact"), D2, root)
    assert out is not None and "Calendar date changed" in out

    out = sl.dispatch(base("SessionStart", cwd=repo, source="compact"), D2, root)
    assert out is not None and "Calendar date changed" in out, "compact перевипускає щоразу"

    out1 = sl.dispatch(base("UserPromptSubmit", cwd=repo), D2, root)
    out2 = sl.dispatch(base("UserPromptSubmit", cwd=repo), D2, root)
    fired = [o for o in (out1, out2) if o and "Calendar date changed" in o]
    assert len(fired) == 1, f"DATE на двох UserPromptSubmit того самого дня має спрацювати рівно раз: {fired}"

    out = sl.dispatch(base("SessionStart", cwd=repo, source="resume"), D2, root)
    assert out is None, "resume при незмінному git мусить бути тихим"

    out = sl.dispatch(base("SessionStart", cwd=repo, source="compact"), D2, root)
    assert out is None, "compact одразу після resume мовчить: start_date уже сьогоднішній"


# --- F1: Stop-базис, не UserPromptSubmit-базис ------------------------------

def test_f1_own_commits_are_silent(tmp_path, repo):
    root = tmp_path
    now = 1_800_000_000.0
    sl.dispatch(base("UserPromptSubmit", cwd=repo), now, root)   # базис A

    for i in range(12):
        _git(repo, "commit", "-q", "--allow-empty", "-m", f"agent-commit-{i}")

    sl.dispatch(base("Stop", cwd=repo), now + 10, root)          # знімок B, тиша
    out = sl.dispatch(base("UserPromptSubmit", cwd=repo), now + 20, root)
    assert out is None, "12 власних комітів між Stop не мають звітуватись"


def test_f1_external_commit_after_stop_is_ahead(tmp_path, repo):
    root = tmp_path
    now = 1_800_000_000.0
    sl.dispatch(base("UserPromptSubmit", cwd=repo), now, root)   # базис A
    sl.dispatch(base("Stop", cwd=repo), now + 5, root)           # базис лишається A

    _git(repo, "commit", "-q", "--allow-empty", "-m", "external")   # зовнішній коміт до B

    out = sl.dispatch(base("UserPromptSubmit", cwd=repo), now + 10, root)
    assert out is not None
    assert "1 new commit(s)" in out

    # TEST_GAP-1: базис мусить зрушити НА ЗВІТІ. Без цього регресія, що
    # перестала його рухати, повторювала б той самий факт на кожному
    # промпті вічно: і жоден тест би не впав.
    again = sl.dispatch(base("UserPromptSubmit", cwd=repo), now + 20, root)
    assert again is None, f"той самий факт повторено: {again!r}"


def test_quality2_stop_before_any_session_start_still_anchors_date(tmp_path, repo):
    """Stop, побачений до будь-якого SessionStart, мусить проставити
    start_date. Інакше наступний UserPromptSubmit піде гілкою «стан є»,
    ніколи не заанкорить, і DATE тихо мертвий до кінця сесії."""
    root = tmp_path
    now = 1_800_000_000.0                      # 2027-01-15 у будь-якій зоні
    sl.dispatch(base("Stop", cwd=repo), now, root)
    saved = sl.load_state(sl.state_path(root, "s1"))
    assert saved is not None and saved.get("start_date"), "Stop не заанкорив дату"

    # наступний день -> DATE мусить прозвучати, а не змовкнути назавжди
    out = sl.dispatch(base("UserPromptSubmit", cwd=repo), now + 86_400, root)
    assert out is not None and "Calendar date changed" in out


# --- F2: перемикання гілки: MOVED, без ancestor/лічильника ----------------

def test_f2_branch_moves_are_moved_not_ancestry_claims(tmp_path, repo):
    root = tmp_path
    now = 1_800_000_000.0
    sl.dispatch(base("UserPromptSubmit", cwd=repo), now, root)   # базис main@A

    _git(repo, "checkout", "-q", "-b", "feature")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "on-feature")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "checkout", "-q", "-b", "divergent")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "on-divergent")

    out = sl.dispatch(base("UserPromptSubmit", cwd=repo), now + 10, root)
    assert out is not None
    assert "ancestor" not in out
    assert not __import__("re").search(r"\d+ new commit", out)
    assert "Git position changed" in out


def test_f2_control_rebase_same_branch_is_diverged(tmp_path, repo):
    root = tmp_path
    now = 1_800_000_000.0
    # Базис береться ПІСЛЯ c2: перепис мусить зсунути той самий коміт,
    # який агент бачив, інакше старий SHA лишається предком і факт
    # вироджується в AHEAD, нічого не перевіривши.
    _git(repo, "commit", "-q", "--allow-empty", "-m", "c2")
    sl.dispatch(base("UserPromptSubmit", cwd=repo), now, root)
    _git(repo, "commit", "--amend", "--allow-empty", "-q", "-m", "c2-rebased")
    out = sl.dispatch(base("UserPromptSubmit", cwd=repo), now + 10, root)
    assert out is not None
    assert "is not an ancestor of" in out


# --- властивості -------------------------------------------------------------

def _run_main(raw_stdin: str, env=None, timeout=10):
    """stdin з файлу, не з пайпа: хук легітимно виходить, не дочитавши
    stdin, і пайп дав би тесту SIGPIPE, що не стосується хука."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        fh.write(raw_stdin)
        name = fh.name
    try:
        with open(name) as stdin:
            return subprocess.run([sys.executable, str(ENTRY)], stdin=stdin,
                                  capture_output=True, text=True, timeout=timeout,
                                  env=dict(os.environ, **(env or {})))
    finally:
        os.unlink(name)


@pytest.mark.parametrize("raw", [
    "", "   ", "not json at all", '{"truncated":', "[]", "null",
    '{"hook_event_name": 42}', '{"hook_event_name": "Unknown"}',
    '{"hook_event_name":"Stop"}',
    '{"hook_event_name":"Stop","session_id":null}',
    '{"x":"' + "A" * 200000 + '"}',
])
def test_never_crashes_and_never_emits_on_bad_input(raw, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    proc = _run_main(raw)
    assert proc.returncode == 0, f"ненульовий вихід на {raw[:40]!r}"
    assert proc.stdout == "", f"сміття в stdout: {proc.stdout[:120]!r}"


def test_full_roundtrip_does_not_hang(tmp_path, monkeypatch, repo):
    """Стеля не з паперу: просто доказ, що процес завершується
    (спека §13.6: реальна цифра round-trip ще не заміряна на цільових
    ОС, це відкрите питання, не перевірений бюджет)."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    payload = json.dumps(base("UserPromptSubmit", cwd=repo))
    proc = _run_main(payload, timeout=10)
    assert proc.returncode == 0


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO needs POSIX")
def test_transcript_is_never_opened(tmp_path, monkeypatch, repo):
    """transcript_path вказує на FIFO без читача: якби плагін спробував
    його відкрити на читання, процес би завис. subprocess-таймаут ловить
    це напряму: надійніше за st_atime, який relatime/noatime роблять
    незастосовним."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    fifo = tmp_path / "transcript.jsonl"
    os.mkfifo(fifo)
    payload = json.dumps(base("SessionStart", cwd=repo, source="startup",
                              transcript_path=str(fifo)))
    proc = _run_main(payload, timeout=5)
    assert proc.returncode == 0


def test_output_shape_when_it_speaks(tmp_path, monkeypatch, repo):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    _run_main(json.dumps(base("SessionStart", cwd=repo, source="startup")))
    state_file = tmp_path / "sincelast" / "s1.json"
    data = json.loads(state_file.read_text())
    data["start_date"] = "1999-01-01"     # force date_changed
    state_file.write_text(json.dumps(data))
    proc = _run_main(json.dumps(base("UserPromptSubmit", cwd=repo)))
    out = json.loads(proc.stdout)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "UserPromptSubmit"
    assert "additionalContext" in hso
    assert "additionalContext" not in out, "голий additionalContext верхнього рівня"


def test_disable_env_silences_everything(tmp_path, monkeypatch, repo):
    """SINCELAST_DISABLE (спека §11, §12b): будь-яке значення, крім
    0/false, вимикає плагін цілком: ні виводу, ні запису стану."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    proc = _run_main(json.dumps(base("SessionStart", cwd=repo, source="startup")),
                     env={"SINCELAST_DISABLE": "1"})
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert not (tmp_path / "sincelast").exists(), "вимкнений плагін не пише стан"


# --- примітка про наслідок, наскрізно ------------------------------------

def test_git_fact_carries_the_stale_note(tmp_path, repo):
    root, now = tmp_path, 1_800_000_000.0
    sl.dispatch(base("UserPromptSubmit", cwd=repo), now, root)
    sl.dispatch(base("Stop", cwd=repo), now + 5, root)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "external")
    out = sl.dispatch(base("UserPromptSubmit", cwd=repo), now + 10, root)
    assert out is not None and "1 new commit(s)" in out
    assert "may be stale" in out, out


def test_note_does_not_depend_on_how_long_the_gap_was(tmp_path, repo):
    """Секунда чи доба: наслідок для прочитаних файлів однаковий."""
    for gap in (1, 86_400):
        root = tmp_path / f"g{gap}"
        root.mkdir()
        now = 1_800_000_000.0
        sl.dispatch(base("UserPromptSubmit", cwd=repo), now, root)
        sl.dispatch(base("Stop", cwd=repo), now + 5, root)
        _git(repo, "commit", "-q", "--allow-empty", "-m", f"ext-{gap}")
        out = sl.dispatch(base("UserPromptSubmit", cwd=repo), now + 5 + gap, root)
        assert out is not None and "may be stale" in out, (gap, out)


def test_date_alone_never_carries_the_note(tmp_path, repo):
    """Примітка пояснює зміну git. Сама дата її не отримує: нічого не
    рухалось у дереві, тож прочитане не застаріло."""
    root, now = tmp_path, 1_800_000_000.0
    sl.dispatch(base("SessionStart", source="startup", cwd=repo), now, root)
    out = sl.dispatch(base("UserPromptSubmit", cwd=repo), now + 86_400, root)
    assert out is not None and "Calendar date changed" in out
    assert "may be stale" not in out, out
