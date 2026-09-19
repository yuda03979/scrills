#!/bin/sh
# Installs scrills: links the command onto your PATH, and offers the skill to Claude Code and Pi.
#
# Two ways in, one script. Run it from a clone (./install.sh, or sh install.sh) and it installs
# that clone. Pipe it from the internet and it clones first, into ~/.local/share/scrills, then
# installs that. Either way the command is a symlink into the repo, so `git pull` is the upgrade
# path and the CLI can still find SKILL.md beside it.
#
# Knobs, all optional: SCRILLS_BIN (default ~/.local/bin), SCRILLS_SRC (default
# ~/.local/share/scrills, only used when cloning), SCRILLS_REPO (default the github url),
# PI_CODING_AGENT_DIR (Pi's config root), --no-skill (skip the harness skill links).
#
# Nothing here touches ~/.scrills - that is your scrill library, and it stays yours.

set -eu

REPO=${SCRILLS_REPO:-https://github.com/yuda03979/scrills.git}
SRC=${SCRILLS_SRC:-$HOME/.local/share/scrills}
BIN=${SCRILLS_BIN:-$HOME/.local/bin}
CLAUDE_SKILLS=$HOME/.claude/skills
PI_AGENT=${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}
PI_SKILLS=$PI_AGENT/skills
want_skill=yes

for arg in "$@"; do
    case $arg in
        --no-skill) want_skill=no ;;
        -h|--help) sed -n '2,13p' "$0" 2>/dev/null || true; exit 0 ;;
        *) echo "install.sh: unknown argument $arg" >&2; exit 2 ;;
    esac
done

here=
if [ -f "$0" ]; then
    here=$(cd "$(dirname "$0")" && pwd)
fi

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

linked_skills=
link_skill() {
    skills=$1
    if [ -e "$skills/scrills" ] && [ ! -L "$skills/scrills" ]; then
        echo "skipped  $skills/scrills exists and is not a symlink - left untouched"
    else
        ln -sfn "$root/scrills" "$skills/scrills"
        linked_skills="$linked_skills $skills/scrills"
        echo "linked   $skills/scrills -> $root/scrills"
    fi
}

if [ "$want_skill" = yes ]; then
    if [ -d "$CLAUDE_SKILLS" ]; then
        link_skill "$CLAUDE_SKILLS"
    fi
    if [ -d "$PI_AGENT" ]; then
        mkdir -p "$PI_SKILLS"
        link_skill "$PI_SKILLS"
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
echo "uninstall:   rm -f $BIN/scrills$linked_skills"
echo "             your library at ~/.scrills is yours - this never touches it"
