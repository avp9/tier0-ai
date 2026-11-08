#!/usr/bin/env bash
# Make the zip file to hand in: the code, the docs, and (by default) the prebuilt search index,
# so whoever receives it can run the app with one command and only an OpenAI key.
#
#   scripts/package.sh                 -> dist/tier0-capstone-<date>.zip
#   scripts/package.sh --no-index      leave out the prebuilt index (zip is ~270 MB smaller; the
#                                      first run then builds it: 15 to 40 minutes + OpenAI cost)
#   scripts/package.sh --output FILE   choose the file name
#
# Never includes .env or any key: it also scans what it is about to zip for key-like text and
# stops if it finds any. Files listed in scripts/.packignore are left out. What is in the zip is
# what is in your working folder (committed or not), minus ignored files.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
WITH_INDEX=1; OUTPUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-index) WITH_INDEX=0 ;;
    --output) OUTPUT="$2"; shift ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1 (try --help)" >&2; exit 2 ;;
  esac
  shift
done
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
command -v zip >/dev/null || die "zip is required."
command -v rsync >/dev/null || die "rsync is required."

NAME="tier0-ai"
OUTPUT="${OUTPUT:-dist/tier0-capstone-$(date +%Y%m%d).zip}"
case "$OUTPUT" in /*) ;; *) OUTPUT="$ROOT/$OUTPUT" ;; esac
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
LIST="$WORK/files.txt"

# 1. Every file that is not git-ignored (or, outside git, every file), minus the .packignore list.
if git rev-parse --git-dir >/dev/null 2>&1; then
  git ls-files -co --exclude-standard | sort -u > "$WORK/all.txt"
else
  find . -type f | sed 's|^\./||' | sort -u > "$WORK/all.txt"
fi
PATTERNS="$WORK/patterns.txt"
grep -Ev '^\s*(#|$)' scripts/.packignore > "$PATTERNS"
: > "$LIST"
while IFS= read -r file; do
  [[ -e "$file" ]] || continue
  skip=0
  while IFS= read -r pattern; do
    case "$pattern" in
      */) [[ "/$file" == *"/$pattern"* ]] && skip=1 ;;       # a folder, anywhere
      \*.*) [[ "$file" == $pattern ]] && skip=1 ;;           # *.ext
      *) [[ "$file" == "$pattern" || "$(basename "$file")" == "$pattern" ]] && skip=1 ;;
    esac
    [[ $skip == 1 ]] && break
  done < "$PATTERNS"
  [[ $skip == 0 ]] && printf '%s\n' "$file" >> "$LIST"
done < "$WORK/all.txt"

# 2. The prebuilt index (git-ignored, but wanted).
if [[ "$WITH_INDEX" == 1 ]]; then
  if [[ -f .data/chroma/chroma.sqlite3 && -f .data/chroma/sparse/meta.json ]]; then
    find .data/chroma -type f >> "$LIST"
  else
    echo "Note: no complete index in .data/chroma (run scripts/run-local.sh once to get one); packaging without it."
    WITH_INDEX=0
  fi
fi

# 3. Refuse to package anything that looks like a key.
LEAKS="$(xargs -I{} grep -IlE 'sk-(proj-)?[A-Za-z0-9_-]{32,}|lsv2_(pt|sk)_[A-Za-z0-9]{20,}|ATATT[A-Za-z0-9_=-]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY-----' {} < "$LIST" 2>/dev/null || true)"
if [[ -n "$LEAKS" ]]; then
  printf 'Stopped: these files look like they contain a key:\n%s\n' "$LEAKS" >&2
  exit 1
fi

# 4. Zip it with a single top-level folder.
STAGE="$WORK/stage/$NAME"; mkdir -p "$STAGE"
COPYFILE_DISABLE=1 rsync -a --files-from="$LIST" ./ "$STAGE/"
chmod +x "$STAGE"/scripts/*.sh
mkdir -p "$(dirname "$OUTPUT")"
rm -f "$OUTPUT"
(cd "$WORK/stage" && zip -qr -X "$OUTPUT" "$NAME")

# 5. Check the result.
LISTING="$(unzip -l "$OUTPUT")"
grep -qE "$NAME/scripts/run-local.sh" <<< "$LISTING" || die "The zip is missing scripts/run-local.sh."
if grep -qE "$NAME/\.env$" <<< "$LISTING"; then die "The zip contains .env. Something is wrong."; fi
FILES="$(tail -n 1 <<< "$LISTING" | awk '{print $2}')"
SIZE="$(du -h "$OUTPUT" | awk '{print $1}')"
echo "Created ${OUTPUT#"$ROOT"/} ($SIZE, $FILES files, prebuilt index: $([[ $WITH_INDEX == 1 ]] && echo yes || echo no))"
echo "To run it: unzip, cd $NAME, then ./scripts/run-local.sh   (see RUN.md inside)"
