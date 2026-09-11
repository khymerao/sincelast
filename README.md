# sincelast

sincelast tells your Claude Code agent when the calendar date or the git HEAD moved behind its back — and says nothing otherwise.

- **DATE.** The system prompt carries the date set once at process start. A session that outlives midnight keeps that stale date — sincelast says when today's date has moved.
- **GIT.** The agent believes it left HEAD/branch where its own last turn ended. sincelast says when that position moved — externally, or in a way the agent's own commits don't explain.

The plugin does not advise. It states a fact and stays silent otherwise. Every sentence it can emit is a template constant in `hooks/sincelast.py`; there is no free-form generated text.

## Requirements

- **`python3` on PATH.** On stock macOS without Xcode Command Line Tools, invoking `python3` opens an "Install Command Line Developer Tools?" dialog. Install ahead of time: `xcode-select --install`.
- **bash is required — including on Windows.** Hook registration uses `"shell": "bash"` and a trailing `|| true`; under a plain PowerShell invocation `|| true` is a syntax error and the hook never fails open. On Windows, use the bash that ships with Git for Windows.
- **git — optional.** DATE works without a repository; GIT facts simply stay silent.
- No third-party packages. Python 3 standard library only.

## Install

> 🇺🇦 [Українською](README.uk.md)

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

93 tests. They run offline and create no files outside `tmp_path`.

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
