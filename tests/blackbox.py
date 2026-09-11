#!/usr/bin/env python3
"""Blackbox suite for sincelast: the process, not the functions.

Runs hooks/sincelast.py as a separate process with real stdin, against
real git repositories, and checks only what is observable from outside:
exit code, stdout, stderr, on-disk state, elapsed time. Needs no pytest.

    python3 tests/blackbox.py hooks/sincelast.py

It exists because it found a bug the unit tests could not: prune_state
deleting a concurrent writer's mkstemp file mid-write, losing another
session's state silently. Unit tests exercise functions one at a time;
this exercises processes racing each other.

Чорноскриньковий набір: процес, а не функції.

Запускає hooks/sincelast.py як окремий процес зі справжнім stdin,
проти справжніх git-репозиторіїв, і перевіряє лише те, що видно
ззовні: код виходу, stdout, stderr, стан на диску, час.
"""
import json, os, shutil, subprocess, sys, tempfile, time, pathlib, stat

PLUGIN = sys.argv[1] if len(sys.argv) > 1 else "hooks/sincelast.py"
PY = sys.executable
passed, failed, notes = 0, [], []

def ok(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
    else:
        failed.append(f"{name}: {detail}")

def note(text):
    notes.append(text)

def run(payload, state_home, env_extra=None, timeout=20):
    env = dict(os.environ, XDG_STATE_HOME=state_home)
    env.pop("SINCELAST_DISABLE", None)
    env.pop("SINCELAST_DEBUG", None)
    if env_extra:
        env.update(env_extra)
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        fh.write(raw); name = fh.name
    try:
        with open(name) as stdin:
            t0 = time.monotonic()
            p = subprocess.run([PY, PLUGIN], stdin=stdin, capture_output=True,
                               text=True, timeout=timeout, env=env)
            return p, (time.monotonic() - t0) * 1000
    finally:
        os.unlink(name)

def git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                           "-c", "user.name=t", *args],
                          capture_output=True, text=True, check=check)

def new_repo(commits=1):
    d = tempfile.mkdtemp()
    git(d, "init", "-q", "-b", "main")
    for i in range(commits):
        git(d, "commit", "-q", "--allow-empty", "-m", f"c{i}")
    return d

def ev(event, sid, cwd, **kw):
    return dict({"hook_event_name": event, "session_id": sid, "cwd": str(cwd),
                 "transcript_path": "/nonexistent/should-never-be-read.jsonl"}, **kw)

def fact(p):
    if not p.stdout.strip():
        return None
    try:
        return json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]
    except Exception:
        return f"<НЕПАРСИБЕЛЬНО: {p.stdout[:80]}>"

# ---------------------------------------------------------------- A. базовий цикл
def group_lifecycle():
    repo, sh = new_repo(), tempfile.mkdtemp()
    sid = "life"
    p, _ = run(ev("SessionStart", sid, repo, source="startup"), sh)
    ok("A1 startup мовчить", p.stdout == "" and p.returncode == 0, repr(p.stdout[:60]))
    p, _ = run(ev("Stop", sid, repo), sh)
    ok("A2 Stop мовчить", p.stdout == "", repr(p.stdout[:60]))
    git(repo, "commit", "-q", "--allow-empty", "-m", "external")
    p, _ = run(ev("UserPromptSubmit", sid, repo), sh)
    f = fact(p)
    ok("A3 зовнішній коміт звітується", f and "1 new commit(s)" in f, repr(f))
    p, _ = run(ev("UserPromptSubmit", sid, repo), sh)
    ok("A4 факт не повторюється", fact(p) is None, repr(fact(p)))
    shutil.rmtree(repo); shutil.rmtree(sh)

# ---------------------------------------------------------------- B. власна робота агента
def group_own_work():
    repo, sh = new_repo(), tempfile.mkdtemp()
    sid = "own"
    run(ev("SessionStart", sid, repo, source="startup"), sh)
    for i in range(12):
        git(repo, "commit", "-q", "--allow-empty", "-m", f"agent-{i}")
    run(ev("Stop", sid, repo), sh)
    p, _ = run(ev("UserPromptSubmit", sid, repo), sh)
    ok("B1 12 власних комітів -> тиша", fact(p) is None, repr(fact(p)))
    shutil.rmtree(repo); shutil.rmtree(sh)

# ---------------------------------------------------------------- C. git edge cases
def group_git_edges():
    sh = tempfile.mkdtemp()
    # C1 не репозиторій
    plain = tempfile.mkdtemp()
    p, _ = run(ev("UserPromptSubmit", "nr", plain), sh)
    ok("C1 не git-репо: exit 0, тиша", p.returncode == 0 and p.stdout == "", repr(p.stdout[:60]))
    # C2 репо без комітів
    empty = tempfile.mkdtemp(); git(empty, "init", "-q", "-b", "main")
    p, _ = run(ev("SessionStart", "e", empty, source="startup"), sh)
    ok("C2 репо без комітів: exit 0", p.returncode == 0, str(p.returncode))
    # C3 detached HEAD
    r = new_repo(3); sid = "det"
    run(ev("SessionStart", sid, r, source="startup"), sh); run(ev("Stop", sid, r), sh)
    git(r, "checkout", "-q", "--detach", "HEAD~1")
    f = fact(run(ev("UserPromptSubmit", sid, r), sh)[0])
    ok("C3 detached -> MOVED без ancestry", f and "detached" in f and "ancestor" not in f, repr(f))
    # C4 перемикання на розбіжну гілку
    r2 = new_repo(2); sid = "div"
    run(ev("SessionStart", sid, r2, source="startup"), sh); run(ev("Stop", sid, r2), sh)
    git(r2, "checkout", "-q", "-b", "other", "HEAD~1")
    git(r2, "commit", "-q", "--allow-empty", "-m", "divergent")
    f = fact(run(ev("UserPromptSubmit", sid, r2), sh)[0])
    ok("C4 розбіжна гілка -> без лічильника комітів",
       f and "new commit(s)" not in f and "ancestor" not in f, repr(f))
    # C5 rebase на тій самій гілці
    r3 = new_repo(3); sid = "reb"
    run(ev("SessionStart", sid, r3, source="startup"), sh); run(ev("Stop", sid, r3), sh)
    git(r3, "reset", "-q", "--hard", "HEAD~1")
    git(r3, "commit", "-q", "--allow-empty", "-m", "rewritten")
    f = fact(run(ev("UserPromptSubmit", sid, r3), sh)[0])
    ok("C5 перепис історії розпізнано", f and ("ancestor" in f or "moved" in f.lower()), repr(f))
    # C6 інший репозиторій під тим самим session_id
    r4 = new_repo(); sid = "swap"
    run(ev("SessionStart", sid, r4, source="startup"), sh); run(ev("Stop", sid, r4), sh)
    other = new_repo(5)
    f = fact(run(ev("UserPromptSubmit", sid, other), sh)[0])
    ok("C6 інший toplevel -> мовчить", f is None, repr(f))
    # C7 назва гілки з лапками й нетиповими символами
    r5 = new_repo(); sid = "weird"
    run(ev("SessionStart", sid, r5, source="startup"), sh); run(ev("Stop", sid, r5), sh)
    git(r5, "checkout", "-q", "-b", "fix/checkout-наслідок")
    git(r5, "commit", "-q", "--allow-empty", "-m", "x")
    p, _ = run(ev("UserPromptSubmit", sid, r5), sh)
    f = fact(p)
    ok("C7 дивна назва гілки не валить", p.returncode == 0 and f is not None, repr(f))
    ok("C7b назва гілки збережена", f and "fix/checkout" in f, repr(f))
    for d in (plain, empty, r, r2, r3, r4, r5, other): shutil.rmtree(d, ignore_errors=True)
    shutil.rmtree(sh)

# ---------------------------------------------------------------- D. fail-open
def group_failopen():
    sh = tempfile.mkdtemp(); repo = new_repo()
    cases = {
        "порожній": "", "пробіли": "   ", "не json": "not json at all",
        "обрізаний": '{"truncated":', "масив": "[]", "null": "null",
        "число як подія": '{"hook_event_name": 42}',
        "невідома подія": '{"hook_event_name":"Nonsense"}',
        "без session_id": '{"hook_event_name":"Stop"}',
        "null session": '{"hook_event_name":"Stop","session_id":null}',
        "вкладений сміттєвий": '{"hook_event_name":"Stop","cwd":{"a":[1,2,{"b":null}]}}',
        "величезний": '{"x":"' + "A" * 500000 + '"}',
        "нуль-байт": '{"hook_event_name":"Stop","session_id":"a\\u0000b"}',
        "юнікод": '{"hook_event_name":"Stop","session_id":"🔥/../../etc"}',
        "cwd не існує": '{"hook_event_name":"UserPromptSubmit","session_id":"x","cwd":"/nope/nope"}',
    }
    # Ці три ДОХОДЯТЬ до except BaseException: решта кейсів вище
    # обробляється явними перевірками і catch-all не задіює. Без них
    # набір не ловив підміну BaseException на вужчий виняток.
    cases.update({
        "cwd як число": '{"hook_event_name":"UserPromptSubmit","session_id":"c1","cwd":12345}',
        "cwd як список": '{"hook_event_name":"UserPromptSubmit","session_id":"c2","cwd":[1,2]}',
        "cwd з NUL": json.dumps({"hook_event_name": "UserPromptSubmit",
                                 "session_id": "c3", "cwd": "/tmp/a\x00b"}),
    })
    bad = []
    for name, raw in cases.items():
        p, _ = run(raw, sh)
        if p.returncode != 0 or p.stdout != "":
            bad.append(f"{name}(rc={p.returncode},out={p.stdout[:40]!r})")
    ok("D1 18 сміттєвих входів: exit 0 і порожній stdout", not bad, "; ".join(bad))
    # D2 git зламаний (підставний git у PATH, що завжди падає)
    fake = tempfile.mkdtemp()
    with open(os.path.join(fake, "git"), "w") as fh:
        fh.write("#!/bin/sh\nexit 99\n")
    os.chmod(os.path.join(fake, "git"), 0o755)
    p, _ = run(ev("UserPromptSubmit", "brk", repo), sh,
               env_extra={"PATH": fake + os.pathsep + os.environ["PATH"]})
    ok("D2 git завжди падає -> exit 0, тиша", p.returncode == 0 and p.stdout == "",
       f"rc={p.returncode} out={p.stdout[:60]!r}")
    # D3 git відсутній узагалі
    p, _ = run(ev("UserPromptSubmit", "nogit", repo), sh, env_extra={"PATH": "/nonexistent"})
    ok("D3 git відсутній -> exit 0", p.returncode == 0, str(p.returncode))
    # D4 стан нечитабельний
    sh2 = tempfile.mkdtemp(); sid = "ro"
    run(ev("SessionStart", sid, repo, source="startup"), sh2)
    d = pathlib.Path(sh2) / "sincelast"
    os.chmod(d, 0o500)
    p, _ = run(ev("Stop", sid, repo), sh2)
    ok("D4 тека стану без запису -> exit 0", p.returncode == 0, str(p.returncode))
    os.chmod(d, 0o700)
    # D5 пошкоджений файл стану
    sid2 = "corrupt"
    run(ev("SessionStart", sid2, repo, source="startup"), sh)
    sf = pathlib.Path(sh) / "sincelast" / f"{sid2}.json"
    sf.write_text('{"git": {"head": ', encoding="utf-8")
    p, _ = run(ev("UserPromptSubmit", sid2, repo), sh)
    ok("D5 пошкоджений стан -> exit 0", p.returncode == 0, str(p.returncode))
    ok("D5b пошкоджений стан перезаписано валідним",
       json.loads(sf.read_text()) is not None, "не перезаписано")
    for d in (fake, repo, sh, sh2): shutil.rmtree(d, ignore_errors=True)

# ---------------------------------------------------------------- E. приватність
def group_privacy():
    sh = tempfile.mkdtemp(); repo = new_repo(); sid = "priv"
    secret = pathlib.Path(tempfile.mkdtemp()) / "transcript.jsonl"
    secret.write_text('{"secret":"AKIA-NEVER-READ-THIS-VALUE"}\n' * 200, encoding="utf-8")
    os.chmod(secret, 0o000)          # нечитабельний: спроба відкрити впала б
    payload = ev("SessionStart", sid, repo, source="startup")
    payload["transcript_path"] = str(secret)
    p, _ = run(payload, sh)
    ok("E1 нечитабельний транскрипт не заважає", p.returncode == 0, str(p.returncode))
    ok("E2 секрет не в stdout", "AKIA" not in p.stdout, p.stdout[:60])
    ok("E3 секрет не в stderr", "AKIA" not in p.stderr, p.stderr[:60])
    os.chmod(secret, 0o600)
    blob = " ".join(f.read_text(errors="ignore")
                    for f in (pathlib.Path(sh) / "sincelast").glob("*.json"))
    ok("E4 секрет не осів у стані", "AKIA" not in blob, blob[:80])
    ok("E5 шлях транскрипту не осів у стані", "transcript" not in blob, blob[:80])
    # E6 жодних мережевих модулів
    src = pathlib.Path(PLUGIN).read_text()
    net = [m for m in ("socket", "urllib", "http.client", "requests", "ssl") if m in src]
    ok("E6 нема мережевих імпортів", not net, str(net))
    shutil.rmtree(sh); shutil.rmtree(repo); shutil.rmtree(secret.parent)

# ---------------------------------------------------------------- F. стан і права
def group_state():
    sh = tempfile.mkdtemp(); repo = new_repo(); sid = "st"
    run(ev("SessionStart", sid, repo, source="startup"), sh)
    d = pathlib.Path(sh) / "sincelast"; f = d / f"{sid}.json"
    ok("F1 тека 0700", stat.S_IMODE(d.stat().st_mode) == 0o700, oct(stat.S_IMODE(d.stat().st_mode)))
    ok("F2 файл 0600", stat.S_IMODE(f.stat().st_mode) == 0o600, oct(stat.S_IMODE(f.stat().st_mode)))
    data = json.loads(f.read_text())
    ok("F3 рівно чотири поля або менше", set(data) <= {"git","start_date","announced_date","updated_ts"}, str(set(data)))
    ok("F4 таймстемп: число", isinstance(data.get("updated_ts"), (int, float)), repr(data.get("updated_ts")))
    ok("F5 нема локалізованих рядків часу",
       not any(isinstance(v, str) and (":" in v and "-" in v) for k, v in data.items() if k != "git"),
       str(data))
    # F6 симлінк як ціль.
    # ВАЖЛИВО: перевіряти "victim не змінився" безглуздо: os.replace
    # замінює сам симлінк, а не пише крізь нього, тож victim цілий і БЕЗ
    # захисту (перевірено окремо). Спостережуваний наслідок захисту в
    # тому, що симлінк ЛИШАЄТЬСЯ симлінком.
    # Через Stop, а НЕ SessionStart: prune_state ходить лише на
    # SessionStart і прибирає симлінк раніше, ніж save_state встигне
    # відмовити. Видалення симлінка з теки 0700 саме по собі безпечне
    # (ціль не чіпається), але воно маскує те, що ми тут перевіряємо.
    sid3 = "sym"; victim = pathlib.Path(sh) / "victim.json"
    victim.write_text('{"canary": true}', encoding="utf-8")
    link = d / f"{sid3}.json"
    link.symlink_to(victim)
    run(ev("Stop", sid3, repo), sh)
    ok("F6 save_state відмовляє писати через симлінк",
       link.is_symlink(), "симлінк замінено звичайним файлом: захист обійдено")
    ok("F6b ціль симлінка не змінено",
       victim.read_text() == '{"canary": true}', victim.read_text()[:60])
    # І окремо: prune прибирає симлінк, але НЕ чіпає його ціль.
    sid4 = "sym2"; link2 = d / f"{sid4}.json"
    link2.symlink_to(victim)
    run(ev("SessionStart", sid4, repo, source="startup"), sh)
    ok("F6c prune прибрав симлінк, ціль жива",
       victim.read_text() == '{"canary": true}', victim.read_text()[:60])
    link.unlink(missing_ok=True)
    # F7 паралельні сесії не заважають
    sids = [f"par{i}" for i in range(6)]
    procs = []
    for s in sids:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write(json.dumps(ev("SessionStart", s, repo, source="startup"))); nm = fh.name
        procs.append((subprocess.Popen([PY, PLUGIN], stdin=open(nm),
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                      env=dict(os.environ, XDG_STATE_HOME=sh)), nm))
    for pr, nm in procs:
        pr.wait(); os.unlink(nm)
    good = sum(1 for s in sids if (d / f"{s}.json").exists()
               and isinstance(json.loads((d / f"{s}.json").read_text()), dict))
    ok("F7 6 паралельних сесій -> 6 валідних станів", good == 6, f"{good}/6")
    # F8 тимчасових файлів не лишилось
    leftovers = [x.name for x in d.iterdir() if x.name.startswith(".tmp") or x.suffix == ".tmp"]
    ok("F8 без тимчасових залишків", not leftovers, str(leftovers))
    shutil.rmtree(sh); shutil.rmtree(repo)

# ---------------------------------------------------------------- G. події сесії
def group_sources():
    sh = tempfile.mkdtemp(); repo = new_repo(); 
    for source in ("startup", "resume", "clear", "fork", "compact"):
        sid = f"src-{source}"
        p, _ = run(ev("SessionStart", sid, repo, source=source), sh)
        ok(f"G {source}: exit 0", p.returncode == 0, str(p.returncode))
    # G6 resume мовчить про дату
    sid = "res"
    run(ev("SessionStart", sid, repo, source="startup"), sh)
    p, _ = run(ev("SessionStart", sid, repo, source="resume"), sh)
    ok("G6 resume мовчить", p.stdout == "", repr(p.stdout[:60]))
    shutil.rmtree(sh); shutil.rmtree(repo)

# ---------------------------------------------------------------- H. конфігурація
def group_config():
    sh = tempfile.mkdtemp(); repo = new_repo(); sid = "cfg"
    run(ev("SessionStart", sid, repo, source="startup"), sh)
    run(ev("Stop", sid, repo), sh)
    git(repo, "commit", "-q", "--allow-empty", "-m", "ext")
    for val in ("1", "true", "yes", "on"):
        p, _ = run(ev("UserPromptSubmit", sid, repo), sh, env_extra={"SINCELAST_DISABLE": val})
        ok(f"H DISABLE={val} мовчить", p.stdout == "", repr(p.stdout[:40]))
    for val in ("0", "false", ""):
        p, _ = run(ev("UserPromptSubmit", sid, repo), sh, env_extra={"SINCELAST_DISABLE": val})
        ok(f"H DISABLE={val!r} НЕ вимикає", p.stdout != "", "мовчав, а мав говорити")
        run(ev("Stop", sid, repo), sh)
        git(repo, "commit", "-q", "--allow-empty", "-m", f"ext-{val}")
    shutil.rmtree(sh); shutil.rmtree(repo)

# ---------------------------------------------------------------- I. продуктивність
def group_perf():
    sh = tempfile.mkdtemp(); repo = new_repo(200)
    sid = "perf"
    run(ev("SessionStart", sid, repo, source="startup"), sh)
    run(ev("Stop", sid, repo), sh)
    git(repo, "commit", "-q", "--allow-empty", "-m", "ext")
    times = [run(ev("UserPromptSubmit", sid, repo), sh)[1] for _ in range(7)]
    times.sort()
    med = times[len(times) // 2]
    note(f"медіана round-trip: {med:.0f} мс (мін {times[0]:.0f}, макс {times[-1]:.0f}): "
         f"включно зі стартом інтерпретатора, репо на 200 комітів")
    ok("I1 round-trip < 2000 мс", med < 2000, f"{med:.0f} мс")
    shutil.rmtree(sh); shutil.rmtree(repo)

for g in (group_lifecycle, group_own_work, group_git_edges, group_failopen,
          group_privacy, group_state, group_sources, group_config, group_perf):
    try:
        g()
    except Exception as e:
        failed.append(f"{g.__name__} ВИКИНУВ: {type(e).__name__}: {e}")

print(f"\n{'='*62}\nПРОЙДЕНО: {passed}   ПРОВАЛЕНО: {len(failed)}\n{'='*62}")
for n in notes:
    print("  ·", n)
if failed:
    print("\nПРОВАЛИ:")
    for f in failed:
        print("  ✗", f)
sys.exit(1 if failed else 0)
