#!/usr/bin/env bash
# Build every figure in this folder to pdf/ and a PNG preview to png/.
#   ./build.sh            build all figures
#   ./build.sh 01-*.tex   build only the named figures
# Needs pdflatex + latexmk (TeX Live/MacTeX with TikZ) and Ghostscript.
set -euo pipefail
cd "$(dirname "$0")"
[ -d /Library/TeX/texbin ] && PATH="/Library/TeX/texbin:$PATH"
for tool in latexmk pdflatex gs; do
  command -v "$tool" >/dev/null || { echo "missing $tool" >&2; exit 1; }
done

mkdir -p build pdf png
commit=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)
git diff --quiet -- . ../crux_scaffold ../run-harness 2>/dev/null || commit="$commit+dirty"
printf '\\def\\cruxcommit{%s}\n' "$commit" > build/commit.tex

if [ "$#" -gt 0 ]; then figs=("$@"); else figs=([0-9]*.tex); fi
status=0
for tex in "${figs[@]}"; do
  name=${tex%.tex}
  if TEXINPUTS=".:build:${TEXINPUTS:-}" latexmk -pdf -interaction=nonstopmode -halt-on-error \
       -outdir=build "$tex" >"build/$name.stdout" 2>&1; then
    cp "build/$name.pdf" "pdf/$name.pdf"
    gs -q -sDEVICE=png16m -r110 -dTextAlphaBits=4 -dGraphicsAlphaBits=4 \
       -o "png/$name.png" "pdf/$name.pdf"
    # Report layout problems that do not stop the build.
    grep -E 'Overfull|Underfull \\hbox \(badness 10000|Missing character' "build/$name.log" \
      | sed "s/^/$name: /" || true
    echo "built $name"
  else
    echo "FAILED $name (see build/$name.log)" >&2
    grep -A3 '^!' "build/$name.log" | head -20 >&2 || true
    status=1
  fi
done
exit $status
