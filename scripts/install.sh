#!/usr/bin/env sh
set -eu

REPO="${1:-$HOME/.skills-src}"
SOURCE="https://github.com/rockszq/skillsctl.git"

if [ ! -d "$REPO" ]; then
    git clone "$SOURCE" "$REPO"
fi

cd "$REPO"
python skillctl.py init
python skillctl.py link
python skillctl.py verify || {
    echo "verify failed; run: python skillctl.py doctor" >&2
    exit 1
}
echo "Restart each agent once so it rescans its skills directory."
