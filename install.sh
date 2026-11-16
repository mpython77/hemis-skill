#!/usr/bin/env bash
# Install this repo as an agent skill: ./install.sh [antigravity|claude]
set -euo pipefail
target="${1:-antigravity}"
case "$target" in
  antigravity) dest="$HOME/.gemini/config/skills/hemis" ;;
  claude)      dest="$HOME/.claude/skills/hemis" ;;
  *) echo "usage: $0 [antigravity|claude]" >&2; exit 2 ;;
esac
src="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$dest"
cp -R "$src/VERSION" "$src/SKILL.md" "$src/AGENTS.md" "$src/references" "$src/scripts" "$src/docs" "$dest/"
python3 "$dest/scripts/hemis_cli.py" doctor --offline >/dev/null && echo "Installed to $dest (doctor OK)"
