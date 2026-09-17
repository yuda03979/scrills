#!/bin/sh
# Installs scrills: links the command onto your PATH, and offers the skill to Claude Code.
#
# Two ways in, one script. Run it from a clone and it installs that clone. Pipe it from the
# internet and it clones first, into ~/.local/share/scrills, then installs that. Either way the
# command is a symlink into the repo, so `git pull` is the upgrade path and the CLI can still
# find SKILL.md beside it.
#
# Knobs, all optional: SCRILLS_BIN (default ~/.local/bin), SCRILLS_SRC (default
# ~/.local/share/scrills, only used when cloning), SCRILLS_REPO (default the github url),
# --no-skill (skip the Claude Code link).
#
# Nothing here touches ~/.scrills - that is your scrill library, and it stays yours.

set -eu

REPO=${SCRILLS_REPO:-https://github.com/yuda03979/scrills.git}
SRC=${SCRILLS_SRC:-$HOME/.local/share/scrills}
BIN=${SCRILLS_BIN:-$HOME/.local/bin}
SKILLS=$HOME/.claude/skills
want_skill=yes

for arg in "$@"; do
    case $arg in
        --no-skill) want_skill=no ;;
        -h|--help) sed -n '2,13p' "$0" 2>/dev/null || true; exit 0 ;;
        *) echo "install.sh: unknown argument $arg" >&2; exit 2 ;;
    esac
done

here=
case $0 in
    */*) here=$(cd "$(dirname "$0")" && pwd) ;;
esac

if [ -n "$here" ] && [ -f "$here/scrills/scripts/scrills" ]; then
    root=$here
    echo "installing from this clone: $root"
else
    command -v git >/dev/null 2>&1 || { echo "install.sh: git is needed to fetch scrills" >&2; exit 1; }
    if [ -d "$SRC/.git" ]; then
        echo "updating $SRC"
        git -C "$SRC" pull --ff-only --quiet
    else
        echo "cloning $REPO into $SRC"
        mkdir -p "$(dirname "$SRC")"
        git clone --quiet "$REPO" "$SRC"
    fi
    root=$SRC
fi

[ -f "$root/scrills/scripts/scrills" ] || { echo "install.sh: no scrills/scripts/scrills under $root" >&2; exit 1; }

mkdir -p "$BIN"
ln -sfn "$root/scrills/scripts/scrills" "$BIN/scrills"
echo "linked   $BIN/scrills -> $root/scrills/scripts/scrills"

linked_skill=
if [ "$want_skill" = yes ] && [ -d "$SKILLS" ]; then
    if [ -e "$SKILLS/scrills" ] && [ ! -L "$SKILLS/scrills" ]; then
        echo "skipped  $SKILLS/scrills exists and is not a symlink - left untouched"
    else
        ln -sfn "$root/scrills" "$SKILLS/scrills"
        linked_skill=$SKILLS/scrills
        echo "linked   $linked_skill -> $root/scrills"
    fi
fi

case ":$PATH:" in
    *":$BIN:"*) ;;
    *) echo; echo "$BIN is not on your PATH. Add this to your shell profile:"; echo "  export PATH=\"$BIN:\$PATH\"" ;;
esac

echo
"$BIN/scrills" --version
echo
echo "start here:  scrills list"
echo "upgrade:     git -C $root pull"
echo "uninstall:   rm -f $BIN/scrills $linked_skill"
echo "             your library at ~/.scrills is yours - this never touches it"
