# sincelast

**[English](#english) · [Українська](#українська)**

---

<a name="english"></a>
sincelast tells your Claude Code agent when the calendar date or the git HEAD moved behind its back — and says nothing otherwise.

- **DATE.** The system prompt carries the date set once at process start. A session that outlives midnight keeps that stale date — sincelast says when today's date has moved.
- **GIT.** The agent believes it left HEAD/branch where its own last turn ended. sincelast says when that position moved — externally, or in a way the agent's own commits don't explain.

The plugin does not advise. It states a fact and stays silent otherwise. Every sentence it can emit is a template constant in `hooks/sincelast.py`; there is no free-form generated text.

## Why a fact, not a timestamp

**Silent until it has something to say.** Nothing changed, nothing is injected. The context stays available for work rather than for a clock.

**Names the stale belief, not a number.** Not "it is now 00:51" but "today is the 12th, not the 11th" — the thing the agent currently has wrong. Not "HEAD = 8d22350" but "the branch moved by one commit since your turn ended".

**Judges deterministically instead of asking the model to judge.** [arXiv 2510.23853](https://arxiv.org/abs/2510.23853) (ACL Findings 2026): given explicit timestamps, no model exceeded 65% alignment with human time perception, and prompt-based alignment has "limited effectiveness". Handing over a number is not handing over a judgement. sincelast makes the comparison itself and emits the conclusion.

**Claims only what it verified.** Ancestry and commit counts are asserted only when the named branch is unchanged; a plain `checkout` to a divergent ref reports a move and guesses nothing about history.

**Does not read your conversation.** `transcript_path` arrives on every call and is deliberately ignored.

**Never blocks a turn.** Every failure path is silence and exit 0.

## Requirements

- **`python3` on PATH.** On stock macOS without Xcode Command Line Tools, invoking `python3` opens an "Install Command Line Developer Tools?" dialog. Install ahead of time: `xcode-select --install`.
- **bash is required — including on Windows.** Hook registration uses `"shell": "bash"` and a trailing `|| true`; under a plain PowerShell invocation `|| true` is a syntax error and the hook never fails open. On Windows, use the bash that ships with Git for Windows.
- **git — optional.** DATE works without a repository; GIT facts simply stay silent.
- No third-party packages. Python 3 standard library only.

## Install

### As a plugin

Clone the repository and add it as a Claude Code plugin. The three hooks
(`SessionStart`, `UserPromptSubmit`, `Stop`) register themselves from
`hooks/hooks.json`, which resolves `${CLAUDE_PLUGIN_ROOT}` for you.

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

Two things worth knowing, both measured rather than assumed:

- **Project settings take effect without a restart.** Editing
  `~/.claude/settings.json` from outside a live session does **not** stick —
  the runtime serialises its in-memory config back over the file.
- **Whether the directory is a git repository is decided at session start.**
  Running `git init` mid-session does not take effect until the next one.

Every hook entry carries an explicit timeout and ends in `|| true`, so a broken
install degrades to silence rather than blocking a turn.

### Verify it works

The plugin is silent when nothing changed — that is the point — so prove it with
a change it must notice:

1. Start a session and let the agent finish a turn (this takes the git baseline).
2. In another terminal: `git commit --allow-empty -m "external"`.
3. Send the agent any message.

The agent's context now carries a line like:

```
Git HEAD on branch "main" moved from 80cce84 to 8d22350: 1 new commit(s)
since the agent's last turn ended.
```

Send another message without committing — this time there is nothing. The
baseline advanced, so the fact is not repeated.

## Data

**What is read:** the hook's own input JSON (`session_id`, `cwd`, `hook_event_name`, `source`) and git state via `git rev-parse` and `git symbolic-ref`.

**The conversation transcript is never read.** `transcript_path` arrives in every hook payload and is deliberately ignored. Transcripts carry tool output in plaintext; a plugin that parses them becomes a second place credentials can leak from.

**What is stored:** one JSON file per session under `${XDG_STATE_HOME:-~/.local/state}/sincelast/`, holding exactly four fields — the process start date, the last-announced date, the last-seen git position (repository path, HEAD SHA, branch name), and a timestamp. The directory is `0700`, files are `0600`, and writes are atomic (temp file plus `os.replace`); a symlink in place of a state file is refused rather than written through.

**Retention:** at each session start, state entries whose own recorded timestamp is older than 30 days are deleted.

**Nothing leaves this machine.** What sincelast reads and stores does not leave the machine — the plugin makes no network calls of any kind, and writes no log files.

**Delete everything:** `rm -rf "${XDG_STATE_HOME:-~/.local/state}/sincelast"`.

**Disable entirely:** set `SINCELAST_DISABLE=1`.

## Configuration

| Env | Default | What it does |
|---|---|---|
| `SINCELAST_DISABLE` | — | any value except empty, `0` or `false` disables the plugin entirely; it exits before reading anything |
| `SINCELAST_DEBUG` | — | on an internal failure, writes the exception type, line and event name to stderr — never the payload or local variables |
| `XDG_STATE_HOME` | `~/.local/state` | parent directory for `sincelast/` state files |

## Timezone

The date is **machine-local**, computed in Python rather than through `date(1)` — locale-sensitive output (Thai Buddhist, Japanese era) would not match the Gregorian date the system prompt carries.

Dev containers default to UTC and do not inherit the host's timezone. A developer in Berlin will then see the date-changed message at 02:00 local time. Fix it at the container level: set `TZ=Europe/Berlin`, or mount `-v /etc/localtime:/etc/localtime:ro`.

## Failure behaviour

Every failure path is silence. The module is wrapped in `try/except BaseException` (imports included), the hook command ends in `|| true`, and the process always exits `0` — a non-zero exit from `Stop` would block the agent. On any error stdout is empty.

## Tests

The plugin itself needs nothing but the standard library. The **tests** need
pytest, which is a development dependency and deliberately not vendored:

```
python3 -m venv .venv
.venv/bin/python -m pip install pytest
.venv/bin/python -m pytest tests/ -q
```

Keep the venv outside your working tree if the repository is under any tool
that measures changed files — a `.venv/` inside the tree shows up as thousands
of modified paths.

96 tests. They run offline and create no files outside `tmp_path`.

There is also a blackbox suite that needs no pytest at all — it runs the
plugin as a process against real repositories and checks only what is
visible from outside:

```
python3 tests/blackbox.py hooks/sincelast.py
```

49 checks, including 18 malformed inputs, a git that always fails, a git
that is absent, parallel sessions, and permissions. It exists because it
found a concurrency bug the unit tests could not see.

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

## License

MIT

---

<a name="українська"></a>

# Українською

sincelast каже твоєму агентові Claude Code, коли календарна дата або git HEAD зрушили за його спиною — і мовчить у решті випадків.

- **DATE.** Системний промпт несе дату, виставлену один раз на старті процесу. Сесія, що переживає північ, тримає ту застарілу дату — sincelast каже, коли сьогоднішнє число змінилось.
- **GIT.** Агент вважає, що лишив HEAD і гілку там, де завершився його власний хід. sincelast каже, коли ця позиція зрушила — ззовні, або так, як власні коміти агента не пояснюють.

Плагін не радить. Він констатує факт і мовчить далі. Кожне речення, яке він здатен вимовити, — це константа-шаблон у `hooks/sincelast.py`; вільно згенерованого тексту немає взагалі.

## Чому факт, а не таймстемп

**Мовчить, поки не має чого сказати.** Нічого не змінилось — нічого не вставляється. Контекст лишається для роботи, а не для годинника.

**Називає конкретне застаріле переконання, а не число.** Не «зараз 00:51», а «сьогодні 12 вересня, а не 11» — тобто саме те, що агент вважає інакше. Не «HEAD = 8d22350», а «гілка зрушила на 1 коміт, відколи ти закінчив хід».

**Судить детерміновано, а не просить судити модель.** [arXiv 2510.23853](https://arxiv.org/abs/2510.23853) (ACL Findings 2026): навіть коли таймстемпи дано, жодна модель не перевищила 65% узгодження з людським сприйняттям часу, а вирівнювання через промпт «має обмежену дієвість». Дати число — не те саме, що дати судження. sincelast робить порівняння сам і віддає готовий висновок.

**Каже лише те, що перевірив.** Твердження про предків і лічильник комітів — лише коли гілка не змінювалась. Звичайний `checkout` на розбіжну гілку дає «позиція змінилась» і жодних здогадів про історію.

**Не читає розмову.** `transcript_path` приходить у кожному виклику і свідомо ігнорується.

**Ніколи не блокує хід.** Будь-який збій — це тиша і код виходу 0.

## Вимоги

- **`python3` у PATH.** На стоковій macOS без Xcode Command Line Tools виклик `python3` відкриває діалог «Install Command Line Developer Tools?». Постав заздалегідь: `xcode-select --install`.
- **bash обовʼязковий — включно з Windows.** Реєстрація хуків використовує `"shell": "bash"` і завершальне `|| true`; під чистим PowerShell `|| true` є синтаксичною помилкою, і хук не впаде м'яко. На Windows користуйся bash із Git for Windows.
- **git — необовʼязковий.** DATE працює без репозиторію; GIT-факти просто мовчать.
- Жодних сторонніх пакетів. Тільки стандартна бібліотека Python 3.

## Встановлення

### Як плагін

Склонуй репозиторій і додай його як плагін Claude Code. Три хуки (`SessionStart`, `UserPromptSubmit`, `Stop`) реєструються самі з `hooks/hooks.json`, який резолвить `${CLAUDE_PLUGIN_ROOT}`.

### Без маркетплейсу

Влий записи хуків у **проєктний** `.claude/settings.json`, замінивши `${CLAUDE_PLUGIN_ROOT}` на абсолютний шлях, куди склонував:

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

Дві речі, які варто знати — обидві виміряні, не припущені:

- **Проєктні налаштування підхоплюються без рестарту.** А от редагування `~/.claude/settings.json` ззовні під час живої сесії **не тримається**: рантайм серіалізує свій конфіг із памʼяті назад поверх файлу.
- **Чи є тека git-репозиторієм, вирішується на старті сесії.** `git init` посеред сесії не подіє до наступної.

Кожен запис хука несе явний `timeout` і завершується `|| true`, тож зламане встановлення деградує в тишу, а не блокує хід.

### Як перевірити, що працює

Плагін мовчить, коли нічого не змінилось — у тому й задум. Тож доведи його зміною, яку він мусить помітити:

1. Почни сесію і дай агентові завершити хід (тут береться базис git).
2. В іншому терміналі: `git commit --allow-empty -m "external"`.
3. Надішли агентові будь-яке повідомлення.

У контексті агента зʼявиться рядок на кшталт:

```
Git HEAD on branch "main" moved from 80cce84 to 8d22350: 1 new commit(s)
since the agent's last turn ended.
```

Надішли ще одне повідомлення, не роблячи коміту — цього разу нічого не буде. Базис зрушив, тож факт не повторюється.

## Дані

**Що читається:** власний вхідний JSON хука (`session_id`, `cwd`, `hook_event_name`, `source`) і стан git через `git rev-parse` та `git symbolic-ref`.

**Транскрипт розмови не читається ніколи.** `transcript_path` приходить у кожному payload хука і свідомо ігнорується. Транскрипти несуть вивід інструментів у відкритому вигляді; плагін, який їх парсить, стає другим місцем, звідки можуть витекти креденшели.

**Що зберігається:** один JSON на сесію під `${XDG_STATE_HOME:-~/.local/state}/sincelast/`, рівно чотири поля — дата старту процесу, остання оголошена дата, остання побачена позиція git (шлях репозиторію, SHA HEAD, назва гілки) і таймстемп. Тека `0700`, файли `0600`, запис атомарний (тимчасовий файл плюс `os.replace`); симлінк на місці файлу стану отримує відмову, а не запис наскрізь.

**Термін зберігання:** на кожному старті сесії видаляються записи, чий власний таймстемп старший за 30 днів.

**Нічого не залишає цю машину.** Плагін не робить жодних мережевих викликів і не пише лог-файлів.

**Видалити все:** `rm -rf "${XDG_STATE_HOME:-~/.local/state}/sincelast"`.

**Вимкнути повністю:** `SINCELAST_DISABLE=1`.

## Конфігурація

| Змінна | Дефолт | Що робить |
|---|---|---|
| `SINCELAST_DISABLE` | — | будь-яке значення, крім порожнього, `0` чи `false`, вимикає плагін цілком; він виходить до того, як щось прочитає |
| `SINCELAST_DEBUG` | — | при внутрішньому збої пише в stderr тип винятку, рядок і назву події — ніколи payload чи локальні змінні |
| `XDG_STATE_HOME` | `~/.local/state` | батьківська тека для файлів стану `sincelast/` |

## Часовий пояс

Дата **машинна**, рахується в Python, а не через `date(1)`: локале-чутливий вивід (тайсько-буддійський календар, японська ера) не збігся б із григоріанською датою, яку несе системний промпт.

Dev-контейнери за замовчуванням у UTC і не успадковують зону хоста. Розробник у Берліні побачить повідомлення про зміну дати о 02:00 за місцевим часом. Лікується на рівні контейнера: `TZ=Europe/Berlin` або монтування `-v /etc/localtime:/etc/localtime:ro`.

## Поведінка при збої

Кожен шлях відмови — це тиша. Модуль обгорнутий у `try/except BaseException` (включно з імпортами), команда хука завершується `|| true`, а процес завжди виходить із кодом `0` — ненульовий вихід зі `Stop` заблокував би агента. За будь-якої помилки stdout порожній.

## Версія Claude Code

Мінімальну версію не міряли, тож тут її не оголошено. Число, якого ніхто не перевіряв, було б заявою, а не вимогою.

Від чого плагін реально залежить:

- `SessionStart` несе поле `source` зі значеннями `startup`, `resume`, `clear`, `fork`, `compact`
- `hookSpecificOutput.additionalContext` на `SessionStart` і `UserPromptSubmit`
- ключі `shell` і `timeout` у `hooks.json`

Якщо твій Claude Code старший за будь-що з цього, плагін мовчить, а не ламається: кожен хук виходить із кодом 0 і нічого не пише на нерозпізнаному payload.

## Тести

Самому плагіну не потрібно нічого, крім стандартної бібліотеки. А от **тестам**
потрібен pytest — це залежність розробки, і вона свідомо не вендориться:

```
python3 -m venv .venv
.venv/bin/python -m pip install pytest
.venv/bin/python -m pytest tests/ -q
```

Тримай venv поза робочим деревом, якщо репозиторій під будь-яким інструментом,
що міряє змінені файли: `.venv/` усередині дерева виглядає як тисячі змінених
шляхів.

96 тестів. Працюють офлайн і не створюють файлів поза `tmp_path`.

Є ще чорноскриньковий набір, якому pytest не потрібен узагалі — він
ганяє плагін процесом проти справжніх репозиторіїв і перевіряє лише те,
що видно ззовні:

```
python3 tests/blackbox.py hooks/sincelast.py
```

49 перевірок: 18 покручених входів, git що завжди падає, git якого нема,
паралельні сесії, права доступу. Він існує тому, що знайшов баг
конкурентності, якого unit-тести не бачили.

## Ліцензія

MIT
