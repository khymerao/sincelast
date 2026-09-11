import os, pathlib, sys, time
import pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "hooks"))
import sincelast as sl


def _with_tz(monkeypatch, tz):
    monkeypatch.setenv("TZ", tz)
    time.tzset()


@pytest.fixture(autouse=True)
def _restore_tz():
    """time.tzset() mutates process-wide state: restoring the env var is
    not enough, the applied zone has to be reset too."""
    original = os.environ.get("TZ")
    yield
    if original is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = original
    time.tzset()


def test_machine_date_is_iso_gregorian(monkeypatch):
    _with_tz(monkeypatch, "Europe/Zagreb")
    assert sl.machine_date(1789112462.0) == "2026-09-11"


def test_machine_date_ignores_locale(monkeypatch):
    """Тайсько-буддійський локаль не сміє змінити григоріанський рядок:
    date(1) протікає локаллю, тому дата рахується в Python."""
    _with_tz(monkeypatch, "Asia/Bangkok")
    monkeypatch.setenv("LC_ALL", "th_TH")
    out = sl.machine_date(1789112462.0)
    assert out.startswith("2026-"), f"локаль протікла в дату: {out}"
    assert len(out) == 10 and out[4] == "-" and out[7] == "-"


def test_date_changed_across_local_midnight(monkeypatch):
    _with_tz(monkeypatch, "Europe/Zagreb")
    before = sl.machine_date(1789106400.0)   # 2026-09-11 08:00 local
    after = 1789164000.0                     # 2026-09-12 00:xx local
    assert sl.date_changed(before, after) is True


def test_date_unchanged_within_day(monkeypatch):
    _with_tz(monkeypatch, "Europe/Zagreb")
    assert sl.date_changed("2026-09-11", 1789112462.0) is False


def test_date_changed_none_stored_is_false():
    """Нема збереженого start_date — нема з чим порівнювати, тиша."""
    assert sl.date_changed(None, 1789112462.0) is False


def test_utc_machine_vs_local_context(monkeypatch):
    """Машина в UTC — дата машинна. README документує наслідок для
    dev-контейнерів (Berlin-розробник о 02:00), а не приховує його."""
    _with_tz(monkeypatch, "UTC")
    assert sl.machine_date(1789156800.0) == "2026-09-11"  # 22:00 UTC

# --- TEST_GAP-2: DST і рух годинника назад -------------------------------

def test_dst_fold_does_not_change_the_date(monkeypatch):
    """Осіннє переведення в Europe/Zagreb: 03:00 -> 02:00 у ту саму добу.
    Година повторюється, календарна дата — ні, тож DATE мусить мовчати."""
    _with_tz(monkeypatch, "Europe/Zagreb")
    # 2026-10-25 01:00 UTC = 03:00 CEST, за мить до згортки
    before = 1792580400.0
    after = before + 3600          # та сама локальна година вдруге, вже CET
    assert sl.machine_date(before) == sl.machine_date(after)
    assert sl.date_changed(sl.machine_date(before), after) is False


def test_dst_spring_forward_does_not_change_the_date(monkeypatch):
    """Весняний стрибок 02:00 -> 03:00: година зникає, дата лишається."""
    _with_tz(monkeypatch, "Europe/Zagreb")
    before = 1774486800.0          # 2026-03-29 01:00 UTC = 02:00 CET
    after = before + 3600
    assert sl.machine_date(before) == sl.machine_date(after)
    assert sl.date_changed(sl.machine_date(before), after) is False


def test_dst_transition_across_midnight_still_reports(monkeypatch):
    """Переведення не маскує справжню зміну доби: через добу після згортки
    дата інша, і DATE зобовʼязаний прозвучати."""
    _with_tz(monkeypatch, "Europe/Zagreb")
    before = 1792580400.0
    assert sl.date_changed(sl.machine_date(before), before + 86400) is True


def test_clock_rewind_within_a_day_is_silent(monkeypatch):
    """NTP зсунув годинник назад, але не через північ — дата та сама."""
    _with_tz(monkeypatch, "Europe/Zagreb")
    later = 1800000000.0
    earlier = later - 1800         # назад на півгодини
    assert sl.date_changed(sl.machine_date(later), earlier) is False


def test_clock_rewind_across_midnight_reports_the_earlier_date(monkeypatch):
    """Пінить НАВМИСНУ поведінку, а не приховує її.

    Годинник зсунуто назад через північ. date_changed порівнює
    відрендерені дати, тож «сьогодні» стає вчорашнім числом і факт
    звучить. Це правильно: системний промпт теж несе машинну дату, і
    якщо машина каже вчора — агент мусить знати, що вони розійшлись."""
    _with_tz(monkeypatch, "Europe/Zagreb")
    after_midnight = 1799971200.0   # 2027-01-15 01:00 Europe/Zagreb
    stored = sl.machine_date(after_midnight)
    rewound = after_midnight - 7200        # назад за північ
    assert sl.machine_date(rewound) != stored
    assert sl.date_changed(stored, rewound) is True

