#!/usr/bin/env python3
# hooks/sincelast.py
"""sincelast — two machine-verified facts for a Claude Code agent.

DATE: the calendar date changed since this session's process started.
GIT: git HEAD/branch moved since the agent's last turn (Stop) ended.

Silence is the norm. The plugin speaks only when a fact is true. Every
sentence comes from a closed set of template constants (see the RENDER
section below) — there is no runtime lexical scan.

The plugin never reads transcript_path. It is present in every hook
payload and is intentionally ignored: the transcript documented carries
tool output in plaintext, and a plugin that parses it becomes a second
place credentials can leak from.
"""
# Imports are guarded because a failure here must never reach the agent:
# a non-zero exit from Stop is a block. The registration's trailing
# `|| true` covers a syntax error, which kills the process before any
# handler can run; this covers an import that fails at runtime.
try:
    import datetime as _dt
    import json
    import os
    import pathlib
    import re
    import subprocess
    import sys
    import tempfile
    import time
except BaseException:  # pragma: no cover - stdlib is always present
    raise SystemExit(0)

DIRNAME = "sincelast"
GIT_TIMEOUT_S = 2.0  # plumbing reads; longer means git is blocked and waiting is pointless
STATE_TTL_DAYS = 30  # spec §8: prune entries older than 30 days
DISABLE_ENV = "SINCELAST_DISABLE"
DEBUG_ENV = "SINCELAST_DEBUG"


# --- state -----------------------------------------------------------------

def state_dir() -> pathlib.Path:
    base = os.environ.get("XDG_STATE_HOME") or str(pathlib.Path.home() / ".local" / "state")
    return pathlib.Path(base) / DIRNAME


def state_path(root, session_id: str) -> pathlib.Path:
    safe = "".join(c for c in (session_id or "") if c.isalnum() or c in "-_")
    return pathlib.Path(root) / f"{safe or 'nosession'}.json"


def load_state(path):
    try:
        if path.is_symlink():
            return None
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None  # missing or corrupt — both mean "no state"
    return data if isinstance(data, dict) else None


def save_state(path, data: dict) -> bool:
    try:
        if path.is_symlink():
            return False  # symlink/TOCTOU — refuse, don't write through it
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)  # atomic, same filesystem
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError:
        return False
    return True


def prune_state(root, now: float, days: int = STATE_TTL_DAYS) -> int:
    """Remove state files whose own `updated_ts` field is older than
    `days`. Reads the JSON field, not filesystem mtime: mtime survives
    `touch` and copies that don't preserve file attributes. A file that
    can't be read as valid state is removed too — it is dead weight
    either way."""
    cutoff = now - days * 86400
    removed = 0
    try:
        # *.json only — never iterdir(). A concurrent writer's mkstemp
        # temp file sits in this directory, created but not yet filled,
        # until its os.replace lands. iterdir() saw it, load_state
        # returned None, and this loop deleted another process's file
        # mid-write: that writer then failed os.replace and lost its
        # state silently. Temp files carry no .json suffix.
        entries = list(pathlib.Path(root).glob("*.json"))
    except OSError:
        return 0
    for entry in entries:
        try:
            data = load_state(entry)
            ts = data.get("updated_ts") if isinstance(data, dict) else None
            if not isinstance(ts, (int, float)) or ts < cutoff:
                entry.unlink()
                removed += 1
        except OSError:
            continue
    return removed


# --- DATE ------------------------------------------------------------------

def machine_date(ts: float) -> str:
    """Machine-local calendar date, ISO-8601 Gregorian. Computed in
    Python — never via date(1), whose output is locale-sensitive (Thai
    Buddhist, Japanese era, non-ASCII month names) and would not match
    the Gregorian date the system prompt carries."""
    return _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def date_changed(stored, ts: float) -> bool:
    if not stored:
        return False  # no stored anchor — nothing to compare, stay silent
    return machine_date(ts) != stored


# --- GIT -------------------------------------------------------------------

def _git_env():
    # LC_ALL=C: localized git output would break parsing.
    # GIT_OPTIONAL_LOCKS=0: hygiene; rev-parse/symbolic-ref never touch the index.
    return dict(os.environ, LC_ALL="C", GIT_OPTIONAL_LOCKS="0")


def _proc(cwd: str, args, timeout_s: float):
    try:
        return subprocess.run(["git", "-C", cwd, *args], capture_output=True,
                              text=True, timeout=timeout_s, env=_git_env())
    except (OSError, subprocess.SubprocessError):
        return None


def _run(cwd: str, args, timeout_s: float):
    proc = _proc(cwd, args, timeout_s)
    if proc is None:
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def _code(cwd: str, args, timeout_s: float):
    proc = _proc(cwd, args, timeout_s)
    return None if proc is None else proc.returncode


def git_snapshot(cwd: str, timeout_s: float = GIT_TIMEOUT_S):
    toplevel = _run(cwd, ["rev-parse", "--show-toplevel"], timeout_s)
    if not toplevel:
        return None  # not a repo, or git unavailable/timed out
    head = _run(cwd, ["rev-parse", "HEAD"], timeout_s)
    if not head:
        return None  # repo with no commits yet
    branch = _run(cwd, ["symbolic-ref", "-q", "--short", "HEAD"], timeout_s)
    return {"toplevel": toplevel, "head": head, "branch": branch}  # branch=None => detached


def _is_ancestor(cwd: str, old: str, new: str, timeout_s: float = GIT_TIMEOUT_S):
    """0 -> old is an ancestor of new; 1 -> diverged or rewritten;
    anything else -> couldn't tell (timeout, missing object)."""
    code = _code(cwd, ["merge-base", "--is-ancestor", old, new], timeout_s)
    if code in (0, 1):
        return code == 0
    return None


def git_compare(old, new, cwd: str, timeout_s: float = GIT_TIMEOUT_S):
    """Structured fact, or None. Ancestry and commit counts are computed
    only when the named branch is unchanged (spec §6/§9) — a branch
    switch or a detach is always MOVED, never AHEAD or DIVERGED, no
    matter how the histories relate."""
    if not old or not new:
        return None
    if old.get("toplevel") != new.get("toplevel"):
        return None  # different repo/worktree — not our comparison
    if old.get("head") == new.get("head") and old.get("branch") == new.get("branch"):
        return None
    if old.get("branch") == new.get("branch") and new.get("branch") is not None:
        anc = _is_ancestor(cwd, old["head"], new["head"], timeout_s)
        if anc is True:
            count = _run(cwd, ["rev-list", "--count", f"{old['head']}..{new['head']}"], timeout_s)
            if count and count.isdigit() and int(count) > 0:
                return ("AHEAD", {"branch": new["branch"], "old": old["head"],
                                  "new": new["head"], "n": int(count)})
            return ("MOVED", {"old": old, "new": new})  # ancestor, but no countable delta
        if anc is False:
            return ("DIVERGED", {"branch": new["branch"], "old": old["head"],
                                 "new": new["head"]})
        return ("MOVED", {"old": old, "new": new})  # anc is None: couldn't tell
    return ("MOVED", {"old": old, "new": new})  # branch changed or detach transition


# --- RENDER ----------------------------------------------------------------
#
# Every sentence the plugin can utter is a module constant here. No other
# string ever reaches stdout. The guarantee is a test of these constants
# against a word whitelist (tests/test_render.py), not a runtime scan of
# rendered text: substring matching over a trusted branch name broke the
# plugin once already ("fix/checkout" contained "check" in the old
# version, and the dispatcher then dropped every fact, date included).

T_DATE = ("Calendar date changed since this session started: "
          "today is {today} (machine-local), not {start_date}.")
T_GIT_AHEAD = ('Git HEAD on branch "{branch}" moved from {old} to {new}: '
               "{n} new commit(s) since the agent's last turn ended.")
T_GIT_DIVERGED = ('Git HEAD on branch "{branch}" moved from {old} to {new}; '
                  "{old} is not an ancestor of {new}.")
T_GIT_MOVED = ("Git position changed since the agent's last turn ended: "
               "was {old_pos}, now {new_pos}.")
P_BRANCH = 'branch "{branch}" at {sha}'
P_DETACHED = "detached HEAD at {sha}"

# A note, never a fact of its own. It names the consequence of the move
# for what the agent already holds, and is omitted when there is no fact
# to attach to.
#
# It replaced a duration ("That was 14h ago."), which was measured and
# found to change nothing: given the duration, agents answered from
# memory exactly as often as with no note at all, and were wrong exactly
# as often. Given this sentence they re-read the file. A duration has no
# consumer; a named consequence does.
T_STALE = " File contents read before that commit may be stale."

_BRANCH_MAX_LEN = 200  # spec §12b: names longer than this are data, not names
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _sanitize_branch(name) -> str:
    """Strip control characters and cap length. Data sanitization, not a
    lexical filter — the value can still contain arbitrary text,
    including words that would trip a substring-matching censor."""
    if not name:
        return ""
    cleaned = _CONTROL_CHARS.sub("", str(name))
    if len(cleaned) <= _BRANCH_MAX_LEN:
        return cleaned
    # Spec 12b: truncation is marked with an ellipsis, so a reader can tell a
    # long name from a name that merely ends where the cap falls.
    return cleaned[: _BRANCH_MAX_LEN - 1] + "\u2026"


def _short(sha: str) -> str:
    return (sha or "")[:7]


def _position(branch, sha: str) -> str:
    if branch is None:
        return P_DETACHED.format(sha=_short(sha))
    return P_BRANCH.format(branch=_sanitize_branch(branch), sha=_short(sha))


def render_date(today: str, start_date: str) -> str:
    return T_DATE.format(today=today, start_date=start_date)


def with_stale(fact: str):
    """Attach the staleness note to a git fact. No fact, no note.

    Unconditional: if HEAD moved at all, anything read before it may
    differ from disk. There is no threshold, because there is no
    quantity to compare against."""
    if not fact:
        return fact
    return fact + T_STALE


def render_git(kind: str, params: dict) -> str:
    if kind == "AHEAD":
        return T_GIT_AHEAD.format(branch=_sanitize_branch(params["branch"]),
                                  old=_short(params["old"]), new=_short(params["new"]),
                                  n=params["n"])
    if kind == "DIVERGED":
        return T_GIT_DIVERGED.format(branch=_sanitize_branch(params["branch"]),
                                     old=_short(params["old"]), new=_short(params["new"]))
    if kind == "MOVED":
        old, new = params["old"], params["new"]
        return T_GIT_MOVED.format(old_pos=_position(old.get("branch"), old["head"]),
                                  new_pos=_position(new.get("branch"), new["head"]))
    raise ValueError(f"unknown git fact kind: {kind!r}")


# --- DISPATCH --------------------------------------------------------------

def dispatch(payload: dict, now: float, root):
    """Pure(-ish) core: no stdin/stdout, no sys.exit. Returns the
    additionalContext string, or None for silence. `root` is the state
    directory (tests pass tmp_path; main() passes state_dir())."""
    event = payload.get("hook_event_name")
    session_id = str(payload.get("session_id") or "")
    cwd = payload.get("cwd") or "."
    path = state_path(root, session_id)
    saved = load_state(path)
    today = machine_date(now)

    if event == "Stop":
        # Snapshot git and own the baseline. The position at the end of
        # the agent's turn is what the agent saw; anything else at the
        # next prompt is external. Nothing is ever reported here.
        data = dict(saved or {})
        data["git"] = git_snapshot(cwd, GIT_TIMEOUT_S)
        data["updated_ts"] = now
        # A Stop seen before any SessionStart would otherwise write a record
        # with no start_date. The next UserPromptSubmit takes the "state
        # exists" branch, never anchors, and date_changed(None, ...) is False
        # forever after — DATE silently dead for this session with no error.
        # Anchor here rather than leave a silent-disable path.
        data.setdefault("start_date", today)
        save_state(path, data)
        return None

    if event == "SessionStart":
        source = payload.get("source")
        if source in ("startup", "resume", "clear", "fork"):
            new_git = git_snapshot(cwd, GIT_TIMEOUT_S)
            fact = None
            if source == "resume" and saved is not None:
                # The agent carries an old belief from the previous
                # process — compare against it before re-anchoring.
                cmp_ = git_compare(saved.get("git"), new_git, cwd, GIT_TIMEOUT_S)
                if cmp_ is not None:
                    fact = with_stale(render_git(*cmp_))
            # startup|clear|fork: no prior belief exists, snapshot silently.
            save_state(path, {"start_date": today, "announced_date": None,
                              "updated_ts": now, "git": new_git})
            prune_state(root, now, STATE_TTL_DAYS)
            return fact
        if source == "compact":
            # compact never touches the git baseline or start_date — a
            # mid-turn compaction is the agent's own work in progress,
            # and the date line it drops from context is re-emitted.
            if saved is None or not date_changed(saved.get("start_date"), now):
                return None
            return render_date(today, saved["start_date"])
        return None  # unknown source: stay silent

    if event == "UserPromptSubmit":
        if saved is None:
            # First observation for this session_id: register the
            # baseline, don't report against nothing.
            save_state(path, {"start_date": today, "announced_date": None,
                              "updated_ts": now,
                              "git": git_snapshot(cwd, GIT_TIMEOUT_S)})
            return None

        facts = []
        if date_changed(saved.get("start_date"), now) and saved.get("announced_date") != today:
            facts.append(render_date(today, saved["start_date"]))
            saved["announced_date"] = today

        new_git = git_snapshot(cwd, GIT_TIMEOUT_S)
        cmp_ = git_compare(saved.get("git"), new_git, cwd, GIT_TIMEOUT_S)
        if cmp_ is not None:
            facts.append(with_stale(render_git(*cmp_)))
        saved["git"] = new_git
        saved["updated_ts"] = now
        save_state(path, saved)
        return "\n".join(facts) if facts else None

    return None  # unregistered event: stay silent


def _disabled() -> bool:
    value = os.environ.get(DISABLE_ENV)
    if value is None:
        return False
    return value.strip().lower() not in ("", "0", "false")


def main() -> int:
    if _disabled():
        return 0
    if sys.stdin.isatty():
        return 0
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except ValueError:
        return 0
    if not isinstance(payload, dict):
        return 0
    event = payload.get("hook_event_name")
    if not isinstance(event, str) or not event:
        return 0
    try:
        text = dispatch(payload, time.time(), state_dir())
    except BaseException as exc:
        if os.environ.get(DEBUG_ENV):
            # Type, module, line, event — never the payload or locals().
            tb = getattr(exc, "__traceback__", None)
            line = tb.tb_lineno if tb is not None else 0
            print(f"sincelast: {type(exc).__name__} at {__name__}:{line} during {event}",
                  file=sys.stderr)
        return 0
    if not text:
        return 0
    # Only this shape reaches the model: a bare top-level
    # additionalContext is silently discarded by the runtime.
    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {"hookEventName": event, "additionalContext": text}
    }))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BaseException:
        sys.exit(0)
