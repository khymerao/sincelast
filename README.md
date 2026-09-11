# sincelast

**[English](#english) · [Українська](#українська)**

---

<a name="english"></a>
sincelast tells your Claude Code agent when the calendar date or the git HEAD moved behind its back, and says nothing otherwise.

- **DATE.** The system prompt carries the date set once at process start. A session that outlives midnight keeps that stale date. sincelast says when today's date has moved.
- **GIT.** The agent believes it left HEAD/branch where its own last turn ended. sincelast says when that position moved: externally, or in a way the agent's own commits don't explain.

The plugin does not advise. It states a fact and stays silent otherwise. Every sentence it can emit is a template constant in `hooks/sincelast.py`; there is no free-form generated text.

## What it saves you

Concrete things that go wrong without it, and stop going wrong with it.

**Files dated yesterday.** You start a session in the evening. Past midnight the agent creates `docs/2026-09-11-design.md`, writes "today" in a changelog, or dates a commit message. All wrong by one day, and none of it obvious until someone sorts the directory. That naming is common for ADRs, specs, changelogs and migrations, and it is where this plugin's own design document got a wrong date during development.

**Work on a branch that moved.** You pull in another terminal, or a teammate pushes, or you rebase. The agent keeps editing against the tree it last saw. Best case it rebuilds something already fixed; worst case it resolves a conflict against a version that no longer exists.

**"Check git status first."** The instruction you keep repeating so the agent does not act on a stale picture. It stops being necessary: the agent is told when the position actually moved, and told nothing when it did not.

**Coming back after hours.** You leave for lunch, a meeting, a night. The agent has no idea any time passed and continues as if from the last sentence. Now it knows the date rolled over and the branch moved, and it knows both before touching anything.

What it does not do: talk. No line is added when nothing changed, so none of this costs you a turn of noise.

## Why a fact, not a timestamp

**Silent until it has something to say.** Nothing changed, nothing is injected. The context stays available for work rather than for a clock.

**Names the stale belief, not a number.** Not "it is now 00:51" but "today is the 12th, not the 11th": the thing the agent currently has wrong. Not "HEAD = 8d22350" but "the branch moved by one commit since your turn ended".

**Judges deterministically instead of asking the model to judge.** [arXiv 2510.23853](https://arxiv.org/abs/2510.23853) (ACL Findings 2026): given explicit timestamps, no model exceeded 65% alignment with human time perception, and prompt-based alignment has "limited effectiveness". Handing over a number is not handing over a judgement. sincelast makes the comparison itself and emits the conclusion.

**Claims only what it verified.** Ancestry and commit counts are asserted only when the named branch is unchanged; a plain `checkout` to a divergent ref reports a move and guesses nothing about history.

## Requirements

- **`python3` on PATH.** On stock macOS without Xcode Command Line Tools, invoking `python3` opens an "Install Command Line Developer Tools?" dialog. Install ahead of time: `xcode-select --install`.
- **bash is required, including on Windows.** The hook command is bash syntax: `"shell": "bash"` and a trailing `|| true`. Under PowerShell that suffix does not mean the same thing, and the fail-open guarantee is lost. On Windows, use the bash that ships with Git for Windows.
- **git is optional.** DATE works without a repository; GIT facts simply stay silent.
- No third-party packages. Python 3 standard library only.

## Install

### As a plugin

```
claude plugin marketplace add khymerao/sincelast
claude plugin install sincelast@sincelast
```

The three hooks (`SessionStart`, `UserPromptSubmit`, `Stop`) register themselves
from `hooks/hooks.json`, which resolves `${CLAUDE_PLUGIN_ROOT}` for you. To
remove it:

```
claude plugin uninstall sincelast@sincelast
claude plugin marketplace remove sincelast
```

### Without a marketplace

Merge the hook entries into your **project** `.claude/settings.json`, replacing
`${CLAUDE_PLUGIN_ROOT}` with the absolute path where you cloned it:

```json
{
  "hooks": {
    "SessionStart": [{ "matcher": "^(startup|resume|clear|fork|compact)$",
      "hooks": [{ "type": "command", "shell": "bash", "timeout": 5,
        "command": "python3 \"/path/to/sincelast/hooks/sincelast.py\" || true" }] }],
    "Stop": [{ "hooks": [{ "type": "command", "shell": "bash", "timeout": 3,
        "command": "python3 \"/path/to/sincelast/hooks/sincelast.py\" || true" }] }],
    "UserPromptSubmit": [{ "hooks": [{ "type": "command", "shell": "bash", "timeout": 5,
        "command": "python3 \"/path/to/sincelast/hooks/sincelast.py\" || true" }] }]
  }
}
```

Two things worth knowing, both observed rather than assumed:

- **Project settings take effect without a restart.** Editing
  `~/.claude/settings.json` from outside a live session does **not** stick:
  the runtime serialises its in-memory config back over the file.
- **Whether the directory is a git repository is decided at session start.**
  Running `git init` mid-session does not take effect until the next one.

Every hook entry carries an explicit timeout and ends in `|| true`, so a broken
install degrades to silence rather than blocking a turn.

### Verify it works

The plugin is silent when nothing changed (that is the point), so prove it with
a change it must notice:

1. Start a session and let the agent finish a turn (this takes the git baseline).
2. In another terminal: `git commit --allow-empty -m "external"`.
3. Send the agent any message.

The agent's context now carries a line like:

```
Git HEAD on branch "main" moved from 80cce84 to 8d22350: 1 new commit(s)
since the agent's last turn ended.
```

Send another message without committing. This time there is nothing: the
baseline advanced, so the fact is not repeated.

## Data

**What is read:** the hook's own input JSON (`session_id`, `cwd`, `hook_event_name`, `source`) and git state via `git rev-parse` and `git symbolic-ref`.

**The conversation transcript is never read.** `transcript_path` arrives in every hook payload and is deliberately ignored. Transcripts carry tool output in plaintext; a plugin that parses them becomes a second place credentials can leak from.

**What is stored:** one JSON file per session under `${XDG_STATE_HOME:-~/.local/state}/sincelast/`, holding exactly four fields: the process start date, the last-announced date, the last-seen git position (repository path, HEAD SHA, branch name), and a timestamp. The directory is `0700`, files are `0600`, and writes are atomic (temp file plus `os.replace`); a symlink in place of a state file is refused rather than written through.

**Retention:** at each session start, state entries whose own recorded timestamp is older than 30 days are deleted.

**Nothing leaves this machine.** What is read and stored does not leave the machine: no network calls of any kind, no log files.

**Delete everything:** `rm -rf "${XDG_STATE_HOME:-~/.local/state}/sincelast"`.

**Disable entirely:** set `SINCELAST_DISABLE=1`.

## Configuration

| Env | Default | What it does |
|---|---|---|
| `SINCELAST_DISABLE` | unset | any value except empty, `0` or `false` disables the plugin entirely; it exits before reading anything |
| `SINCELAST_DEBUG` | unset | on an internal failure, writes the exception type, line and event name to stderr; never the payload or local variables |
| `XDG_STATE_HOME` | `~/.local/state` | parent directory for `sincelast/` state files |

## Timezone

The date is **machine-local** and matches the Gregorian date the system prompt carries. It is computed in Python rather than through `date(1)`, whose locale-sensitive output (Thai Buddhist, Japanese era) would not.

A dev container that runs in UTC (the usual default, since it does not inherit the host's timezone) shifts the date change: a developer in Berlin sees the message at 02:00 local time. Fix it at the container level: set `TZ=Europe/Berlin`, or mount `-v /etc/localtime:/etc/localtime:ro`.

## Failure behaviour

Every failure path is silence. The module is wrapped in `try/except BaseException` (imports included), the hook command ends in `|| true`, and the process always exits `0`, because a non-zero exit from `Stop` would block the agent. On any error stdout is empty.

## Claude Code version

No minimum version has been measured, so none is declared here. A number nobody
tested would be a claim, not a requirement.

What the plugin actually depends on:

- `SessionStart` carrying a `source` field with the values `startup`, `resume`,
  `clear`, `fork` and `compact`
- `hookSpecificOutput.additionalContext` on `SessionStart` and `UserPromptSubmit`
- the `shell` and `timeout` keys in `hooks.json`

If your Claude Code predates any of these, the plugin stays silent rather than
failing: every hook exits 0 and writes nothing on an unrecognised payload.

## Tests

The plugin itself needs nothing but the standard library. The **tests** need
pytest, which is a development dependency and deliberately not vendored:

```
python3 -m venv ~/.venvs/sincelast
~/.venvs/sincelast/bin/python -m pip install pytest
~/.venvs/sincelast/bin/python -m pytest tests/ -q
```

The venv goes outside the working tree on purpose. Put it in `.venv/` here and
any tool that measures changed files sees thousands of modified paths under
`site-packages`. On Windows the interpreter is `Scripts\python.exe` rather than
`bin/python`.

The unit tests run offline and create no files outside `tmp_path`.

There is also a blackbox suite that needs no pytest at all. It runs the
plugin as a process against real repositories and checks only what is
visible from outside:

```
python3 tests/blackbox.py hooks/sincelast.py
```

It covers malformed inputs, a git that always fails, a git that is absent,
parallel sessions, and permissions. It exists because it found a concurrency
bug the unit tests could not see.

## License

MIT

---

<a name="українська"></a>

# Українською

sincelast каже твоєму агентові в Claude Code, що календарна дата або git HEAD зрушили за його спиною. У решті випадків мовчить.

- **DATE.** Дату агент бачить один раз, коли стартує процес. Сесія перевалила за північ, а дата так і висить учорашня. sincelast каже, коли сьогодні стало іншим числом.
- **GIT.** Агент думає, що HEAD і гілка там, де він їх лишив наприкінці свого ходу. sincelast каже, коли вони зрушили: ззовні або так, як його власні коміти не пояснюють.

Плагін не радить. Каже факт і замовкає. Усе, що він узагалі здатен сказати, лежить константами в `hooks/sincelast.py`. Нічого не генерується на льоту.

## Що це дає на практиці

Конкретні речі, які ламаються без нього і перестають ламатись із ним.

**Файли з учорашньою датою.** Почав сесію ввечері. Після півночі агент створює `docs/2026-09-11-design.md`, пише «сьогодні» в чейнджлозі або ставить дату в коміт. Усе на день назад, і ніхто цього не помічає, поки не гляне на теку відсортовану. Так іменують ADR, специфікації, чейнджлоги, міграції. На такому ж файлі проблема й вилізла, коли робили цей плагін.

**Робота по гілці, яка вже поїхала.** Ти зробив pull в іншому терміналі, або колега запушив, або сам зробив rebase. Агент далі править по тому дереву, яке бачив востаннє. У кращому разі перероблює вже полагоджене, у гіршому розрулює конфлікт проти версії, якої вже нема.

**«Спершу глянь git status».** Те, що доводиться повторювати, аби агент не діяв за застарілою картинкою. Більше не треба: йому кажуть, коли позиція справді зрушила, і мовчать, коли ні.

**Повернення через кілька годин.** Пішов на обід, на зустріч, спати. Агент не має уявлення, що минув час, і продовжує з останнього речення. Тепер він знає, що дата перевалила і гілка з'їхала, причому знає це до того, як щось чіпати.

Чого він не робить: не балакає. Нічого не змінилось, нічого й не додається, тож жоден хід не витрачається на шум.

## Чому факт, а не таймстемп

**Мовчить, поки нема чого сказати.** Нічого не змінилось, нічого не вставляється. Контекст лишається під роботу, а не під годинник.

**Каже, що саме агент має неправильно, а не число.** Не «зараз 00:51», а «сьогодні 12-те, а не 11-те». Не «HEAD = 8d22350», а «гілка зрушила на коміт, відколи ти закінчив хід».

**Рішення приймає сам, а не просить модель.** [arXiv 2510.23853](https://arxiv.org/abs/2510.23853) (ACL Findings 2026): дай моделі таймстемп, і вона все одно не перевалить за 65% узгодження з тим, як час сприймає людина. Вирівнювати це промптом майже не виходить. Число і судження це різні речі. sincelast порівнює сам і віддає готовий висновок.

**Каже тільки те, що перевірив.** Про предків і лічильник комітів говорить, лише поки гілка не мінялась. Звичайний `checkout` на розбіжну гілку дає «позиція змінилась» і жодних здогадок про історію.

## Що треба

- **`python3` у PATH.** На чистій macOS без Xcode Command Line Tools виклик `python3` відкриє віконце «Install Command Line Developer Tools?». Постав заздалегідь: `xcode-select --install`.
- **bash, і на Windows теж.** Команда хука написана на bash: `"shell": "bash"` і `|| true` у кінці. Під PowerShell цей хвіст означає інше, і м'яке падіння втрачається. На Windows бери bash із Git for Windows.
- **git необов'язковий.** DATE працює і без репозиторію, GIT-факти просто мовчать.
- Сторонніх пакетів нема. Тільки стандартна бібліотека Python 3.

## Як поставити

### Плагіном

```
claude plugin marketplace add khymerao/sincelast
claude plugin install sincelast@sincelast
```

Три хуки (`SessionStart`, `UserPromptSubmit`, `Stop`) зареєструються самі з `hooks/hooks.json`, там `${CLAUDE_PLUGIN_ROOT}` підставиться автоматично. Знести:

```
claude plugin uninstall sincelast@sincelast
claude plugin marketplace remove sincelast
```

### Руками, без маркетплейсу

Влий записи хуків у **проєктний** `.claude/settings.json` і заміни `${CLAUDE_PLUGIN_ROOT}` на свій шлях:

```json
{
  "hooks": {
    "SessionStart": [{ "matcher": "^(startup|resume|clear|fork|compact)$",
      "hooks": [{ "type": "command", "shell": "bash", "timeout": 5,
        "command": "python3 \"/шлях/до/sincelast/hooks/sincelast.py\" || true" }] }],
    "Stop": [{ "hooks": [{ "type": "command", "shell": "bash", "timeout": 3,
        "command": "python3 \"/шлях/до/sincelast/hooks/sincelast.py\" || true" }] }],
    "UserPromptSubmit": [{ "hooks": [{ "type": "command", "shell": "bash", "timeout": 5,
        "command": "python3 \"/шлях/до/sincelast/hooks/sincelast.py\" || true" }] }]
  }
}
```

Дві штуки, на які легко напоротись. Обидві не вигадані, а побачені на живому:

- **Проєктний конфіг підхоплюється без рестарту.** А от правити `~/.claude/settings.json` ззовні, поки сесія жива, марно: рантайм тримає конфіг у пам'яті і затре твою правку своєю версією.
- **Чи тека є git-репозиторієм, вирішується на старті сесії.** Зробив `git init` посеред роботи? Подіє тільки з наступної сесії.

У кожному записі стоїть свій `timeout` і `|| true` у кінці, тож криве встановлення дасть тишу, а не зависле повідомлення.

### Як перевірити, що працює

Плагін мовчить, коли нічого не змінилось, у тому й сенс. Тож підсунь йому зміну, яку він мусить помітити:

1. Почни сесію і дай агентові договорити хід. Тут він бере базис git.
2. В іншому терміналі: `git commit --allow-empty -m "external"`.
3. Напиши агентові будь-що.

У контексті агента буде рядок десь такий:

```
Git HEAD on branch "main" moved from 80cce84 to 8d22350: 1 new commit(s)
since the agent's last turn ended.
```

Напиши ще раз, нічого не комітячи. Тиша. Базис зрушив, повторювати нема чого.

## Що з даними

**Що читає:** свій же вхідний JSON (`session_id`, `cwd`, `hook_event_name`, `source`) і стан git через `git rev-parse` та `git symbolic-ref`.

**Транскрипт розмови не читає взагалі.** `transcript_path` приходить у кожному виклику, і плагін його свідомо ігнорує. У транскриптах лежить вивід інструментів відкритим текстом. Плагін, який туди лізе, стає ще одним місцем, звідки можуть витекти креденшели.

**Що пише на диск:** один JSON на сесію в `${XDG_STATE_HOME:-~/.local/state}/sincelast/`, рівно чотири поля: дата старту процесу, остання оголошена дата, остання побачена позиція git (шлях репо, SHA HEAD, гілка) і таймстемп. Тека `0700`, файли `0600`, запис атомарний (тимчасовий файл плюс `os.replace`). Якщо на місці файлу стану лежить симлінк, плагін відмовиться писати, а не піде крізь нього.

**Скільки живе:** на старті сесії викидаються записи, чий власний таймстемп старший за 30 днів.

**Нікуди не йде.** Мережевих викликів нема взагалі, лог-файлів теж.

**Знести все:** `rm -rf "${XDG_STATE_HOME:-~/.local/state}/sincelast"`.

**Вимкнути:** `SINCELAST_DISABLE=1`.

## Налаштування

| Змінна | За замовчуванням | Що робить |
|---|---|---|
| `SINCELAST_DISABLE` | не задано | будь-що, крім порожнього, `0` і `false`, вимикає плагін цілком: він вийде ще до того, як щось прочитає |
| `SINCELAST_DEBUG` | не задано | якщо всередині щось зламалось, кине в stderr тип винятку, рядок і назву події. Payload і локальні змінні не пише ніколи |
| `XDG_STATE_HOME` | `~/.local/state` | де тримати теку `sincelast/` |

## Часовий пояс

Дата береться **машинна**, бо системний промпт несе саме машинну дату, і дві різні дати гірші за одну зміщену. Рахується в Python, а не через `date(1)`: той залежить від локалі, і тайсько-буддійський чи японський ерний календар дадуть рядок, що з григоріанською датою не зійдеться.

Dev-контейнер, що крутиться в UTC (звичайна справа, бо зону хоста він не підхоплює), зсуне момент. Розробник у Берліні побачить повідомлення о другій ночі. Лікується на рівні контейнера: `TZ=Europe/Berlin` або `-v /etc/localtime:/etc/localtime:ro`.

## Якщо щось зламається

Будь-який збій веде в тишу. Модуль обгорнутий у `try/except BaseException` разом з імпортами, команда хука закінчується `|| true`, процес завжди виходить із кодом `0`. Ненульовий вихід зі `Stop` заблокував би агента. За будь-якої помилки stdout порожній.

## Версія Claude Code

Мінімальну не міряли, тож і не пишемо. Число, яке ніхто не перевіряв, це заявка, а не вимога.

Від чого плагін справді залежить:

- `SessionStart` несе поле `source` зі значеннями `startup`, `resume`, `clear`, `fork`, `compact`
- `hookSpecificOutput.additionalContext` на `SessionStart` і `UserPromptSubmit`
- ключі `shell` і `timeout` у `hooks.json`

Якщо твій Claude Code старіший за щось із цього, плагін змовчить, а не зламається: кожен хук вийде з кодом 0 і нічого не напише.

## Тести

Самому плагіну не треба нічого, крім стандартної бібліотеки. А тестам потрібен pytest, і він свідомо не лежить у репо:

```
python3 -m venv ~/.venvs/sincelast
~/.venvs/sincelast/bin/python -m pip install pytest
~/.venvs/sincelast/bin/python -m pytest tests/ -q
```

venv навмисно поза робочим деревом. Поклади його в `.venv/` тут, і будь-який інструмент, що дивиться на змінені файли, побачить тисячі шляхів у `site-packages`. На Windows інтерпретатор лежить у `Scripts\python.exe`, а не в `bin/python`.

Unit-тести працюють офлайн і нічого не створюють поза `tmp_path`.

Є ще чорноскриньковий набір, якому pytest не потрібен зовсім. Він ганяє плагін процесом проти справжніх репозиторіїв і дивиться тільки на те, що видно ззовні:

```
python3 tests/blackbox.py hooks/sincelast.py
```

Покручені входи, git що падає, git якого нема, паралельні сесії, права доступу. Він з'явився тому, що зловив баг конкурентності, якого unit-тести не бачили.

## Ліцензія

MIT
