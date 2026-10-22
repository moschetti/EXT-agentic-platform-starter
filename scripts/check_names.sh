#!/usr/bin/env bash
# Fails if any term listed in .blocked-terms (one per line, not committed) appears in tracked
# or staged files. Use it to keep organisation names, people and internal hostnames out of the repo.
set -euo pipefail
cd "$(dirname "$0")/.."
[[ -f .blocked-terms ]] || { echo "No .blocked-terms file; nothing to check."; exit 0; }
found=0
while IFS= read -r term; do
  [[ -z "$term" || "$term" == \#* ]] && continue
  if git ls-files -co --exclude-standard | grep -v '^.blocked-terms$' | xargs grep -n -i -F -- "$term" 2>/dev/null; then
    found=1
  fi
done < .blocked-terms
[[ $found -eq 0 ]] && echo "OK: no blocked terms found." || { echo "Blocked terms found (above)."; exit 1; }
