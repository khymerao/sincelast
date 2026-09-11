import json, os, stat, pathlib
import pytest
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "hooks"))
import sincelast as sl


def test_state_dir_honors_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    assert sl.state_dir() == tmp_path / "xdg" / "sincelast"


def test_state_dir_falls_back_to_home(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    assert sl.state_dir() == tmp_path / ".local" / "state" / "sincelast"


def test_roundtrip(tmp_path):
    p = sl.state_path(tmp_path, "sess-1")
    assert sl.save_state(p, {"start_date": "2026-09-11", "announced_date": None,
                             "updated_ts": 1.5, "git": None}) is True
    assert sl.load_state(p) == {"start_date": "2026-09-11", "announced_date": None,
                                "updated_ts": 1.5, "git": None}


def test_missing_returns_none(tmp_path):
    assert sl.load_state(sl.state_path(tmp_path, "nope")) is None


def test_corrupt_treated_as_absent(tmp_path):
    p = sl.state_path(tmp_path, "sess-2")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"start_date": "2026', encoding="utf-8")
    assert sl.load_state(p) is None
    assert sl.save_state(p, {"ok": True}) is True
    assert sl.load_state(p) == {"ok": True}


def test_permissions(tmp_path):
    p = sl.state_path(tmp_path, "sess-3")
    sl.save_state(p, {"x": 1})
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert stat.S_IMODE(p.parent.stat().st_mode) == 0o700


def test_refuses_symlink_target(tmp_path):
    p = sl.state_path(tmp_path, "sess-4")
    p.parent.mkdir(parents=True, exist_ok=True)
    victim = tmp_path / "victim.json"
    victim.write_text("{}", encoding="utf-8")
    p.symlink_to(victim)
    assert sl.save_state(p, {"evil": True}) is False
    assert victim.read_text(encoding="utf-8") == "{}"


def test_no_temp_file_left_behind(tmp_path):
    p = sl.state_path(tmp_path, "sess-5")
    sl.save_state(p, {"x": 1})
    leftovers = [f for f in p.parent.iterdir() if f != p]
    assert leftovers == [], f"залишились тимчасові файли: {leftovers}"


def test_prune_reads_updated_ts_not_mtime(tmp_path):
    """Прибирання читає власне поле updated_ts файла, а не mtime ФС,
    mtime переживає touch і копіювання без збереження атрибутів."""
    root = tmp_path
    old = sl.state_path(root, "old-sess")
    new = sl.state_path(root, "new-sess")
    now = 1_800_000_000.0
    sl.save_state(old, {"updated_ts": now - 40 * 86400})
    sl.save_state(new, {"updated_ts": now - 1 * 86400})
    os.utime(old, (now, now))  # mtime свіжий, updated_ts: старий: має все одно видалитись
    assert sl.prune_state(old.parent, now, days=30) == 1
    assert not old.exists() and new.exists()


def test_prune_removes_unreadable_entries(tmp_path):
    d = tmp_path
    junk = d / "junk.json"
    junk.write_text("not json", encoding="utf-8")
    assert sl.prune_state(d, 1_800_000_000.0, days=30) == 1
    assert not junk.exists()

def test_prune_never_touches_a_concurrent_writers_temp_file(tmp_path):
    """Регресія на справжню гонку, знайдену чорноскриньковим прогоном.

    save_state створює mkstemp-файл ПОРОЖНІМ і заповнює його перед
    os.replace. Доти він лежить у теці. prune_state ходив по iterdir(),
    читав його як невалідний стан і видаляв: чужий процес потім падав
    на os.replace, і його стан зникав мовчки. Відтворювалось приблизно
    в одному прогоні з восьми при шести паралельних сесіях."""
    import os, tempfile
    root = tmp_path / "sincelast"
    root.mkdir(parents=True)
    sl.save_state(root / "other.json", {"updated_ts": 9e9})

    fd, tmp = tempfile.mkstemp(dir=str(root), prefix=".tmp-")   # порожній, як у mkstemp
    os.close(fd)

    sl.prune_state(root, now=1e9)

    assert os.path.exists(tmp), "prune видалив тимчасовий файл чужого процесу"
    assert (root / "other.json").exists(), "prune видалив валідний чужий стан"


def test_prune_still_removes_genuinely_old_state(tmp_path):
    """Захист не має зламати саме прибирання: старий стан мусить піти."""
    import os
    root = tmp_path / "sincelast"
    root.mkdir(parents=True)
    old = root / "ancient.json"
    sl.save_state(old, {"updated_ts": 1000.0})
    ancient = 1000.0
    os.utime(old, (ancient, ancient))          # і mtime теж старий
    assert sl.prune_state(root, now=1e9) == 1
    assert not old.exists()


def test_prune_removes_an_unreadable_json_file(tmp_path):
    """Свідомо лишено як було: файл із суфіксом .json, який не читається,
    видаляється: він мертва вага. Часткового .json при нормальному записі
    не буває: save_state пише в mkstemp без суфікса і робить атомарний
    os.replace, тож недописаного .json просто не існує."""
    root = tmp_path / "sincelast"
    root.mkdir(parents=True)
    junk = root / "junk.json"
    junk.write_text('{"updated_ts": ', encoding="utf-8")
    assert sl.prune_state(root, now=1e9) == 1
    assert not junk.exists()

