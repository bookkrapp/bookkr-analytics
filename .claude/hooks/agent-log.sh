#!/usr/bin/env bash
# Append an entry to agent_log.md. Run this as the last action of any session
# that changed files:
#
#   .claude/hooks/agent-log.sh "what you did" ["notes"]
#
# Timestamp, branch and changed-file list are filled in from git.

set -uo pipefail

# Byte ordering, to match session-start.sh / agent-log-stop.sh.
export LC_ALL=C

summary="${1:-}"
notes="${2:-}"

if [ -z "$summary" ]; then
  echo "usage: agent-log.sh \"one-line summary\" [\"notes\"]" >&2
  exit 1
fi

root="${CLAUDE_PROJECT_DIR:-}"
[ -z "$root" ] && root=$(git rev-parse --show-toplevel 2>/dev/null)
[ -z "$root" ] && root=$PWD

log="$root/agent_log.md"
branch=$(git -C "$root" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
stamp=$(date '+%Y-%m-%d %H:%M %Z')

# Changed files: staged, unstaged and untracked, minus machine-local state.
# Built with a read loop rather than mapfile — macOS ships bash 3.2.
files=()
while IFS= read -r line; do
  [ -n "$line" ] && files+=("$line")
done < <(
  git -C "$root" status --porcelain --untracked-files=all 2>/dev/null \
    | sed 's/^...//' \
    | sed 's/.* -> //' \
    | grep -vE '^\.claude/state/' \
    | sort -u
)

count=${#files[@]}
if [ "$count" -gt 12 ]; then
  shown=$(printf '`%s`, ' "${files[@]:0:12}")
  shown="${shown%, } (+$((count - 12)) more)"
elif [ "$count" -gt 0 ]; then
  shown=$(printf '`%s`, ' "${files[@]}")
  shown="${shown%, }"
else
  shown="_none in the working tree — already committed?_"
fi

[ -f "$log" ] || printf '# Agent log\n\nAppend-only. Newest at the bottom. See `CLAUDE.md`.\n' > "$log"

{
  printf '\n---\n\n## %s · `%s`\n\n' "$stamp" "$branch"
  printf '**Summary:** %s\n\n' "$summary"
  [ -n "$notes" ] && printf '**Notes:** %s\n\n' "$notes"
  printf '**Files:** %s\n' "$shown"
} >> "$log"

# Mark this session as logged so the Stop fallback stays quiet.
session=""
[ -f "$root/.claude/state/session-id" ] && session=$(cat "$root/.claude/state/session-id")
if [ -n "$session" ]; then
  mkdir -p "$root/.claude/state/logged"
  touch "$root/.claude/state/logged/$session"
fi

echo "Logged to agent_log.md ($count file(s) changed)."
