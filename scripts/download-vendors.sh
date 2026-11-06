#!/usr/bin/env bash
# web/static/vendor/ is gitignored for repo size reasons.
# Run this script after cloning or when updating versions.
set -euo pipefail
trap 'echo "FAILED at line $LINENO: $BASH_COMMAND" >&2' ERR

usage() {
	cat <<'EOF'
Usage: download-vendors.sh [OPTIONS]

Download vendored JS/CSS/font files into web/static/vendor/.
The directory is gitignored for repo size reasons; run this script
after cloning or when bumping an upstream version.

Options:
  -F, --force    Re-download every file even if it already exists
                 (use after bumping a version in this script).
  -h, --help     Show this help and exit.

Examples:
  ./scripts/download-vendors.sh
  ./scripts/download-vendors.sh -F
EOF
}

FORCE=0
while [ $# -gt 0 ]; do
	case "$1" in
		-F|--force)
			FORCE=1
			shift
			;;
		-h|--help)
			usage
			exit 0
			;;
		--)
			shift
			break
			;;
		*)
			echo "Unknown argument: $1" >&2
			echo >&2
			usage >&2
			exit 2
			;;
	esac
done

if [ "$FORCE" -eq 1 ]; then
	echo "Force mode: re-downloading all files"
fi

VENDOR_DIR="$(dirname "$0")/../web/static/vendor"
mkdir -p "$VENDOR_DIR/fonts"
cd "$VENDOR_DIR"

fetch() {
	local file="$1" url="$2"
	if [ -f "$file" ] && [ "$FORCE" -eq 0 ]; then
		echo "  [skip] $file"
		return 0
	fi
	if ! curl -sfL --max-time 60 -o "$file" "$url"; then
		echo "ERROR: failed to download $url" >&2
		rm -f "$file"
		exit 1
	fi
	echo "  [get]  $file"
}

echo "Fetching vendor libraries..."
fetch htmx.min.js "https://unpkg.com/htmx.org@1.9.10/dist/htmx.min.js"
fetch marked.min.js "https://cdn.jsdelivr.net/npm/marked@12/marked.min.js"
fetch alpine.min.js "https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js"
fetch katex.min.js "https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"
fetch auto-render.min.js "https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js"
fetch katex.min.css "https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css"

echo "Fetching katex fonts (per-file jsdelivr, list from CSS)..."
KATEX_VERSION="0.16.9"
KATEX_FONTS=(
	KaTeX_AMS-Regular
	KaTeX_Caligraphic-Bold
	KaTeX_Caligraphic-Regular
	KaTeX_Fraktur-Bold
	KaTeX_Fraktur-Regular
	KaTeX_Main-Bold
	KaTeX_Main-BoldItalic
	KaTeX_Main-Italic
	KaTeX_Main-Regular
	KaTeX_Math-BoldItalic
	KaTeX_Math-Italic
	KaTeX_SansSerif-Bold
	KaTeX_SansSerif-Italic
	KaTeX_SansSerif-Regular
	KaTeX_Script-Regular
	KaTeX_Size1-Regular
	KaTeX_Size2-Regular
	KaTeX_Size3-Regular
	KaTeX_Size4-Regular
	KaTeX_Typewriter-Regular
)

for font in "${KATEX_FONTS[@]}"; do
	fetch "fonts/${font}.woff2" \
		"https://cdn.jsdelivr.net/npm/katex@${KATEX_VERSION}/dist/fonts/${font}.woff2"
done

echo "Done. Vendor files in $VENDOR_DIR"
echo "  js/css: $(find . -type f \( -name '*.js' -o -name '*.css' \) 2>/dev/null | wc -l | tr -d ' ') files"
echo "  fonts:  $(find fonts/ -type f -name '*.woff2' 2>/dev/null | wc -l | tr -d ' ') woff2 files"
echo "  size:   $(du -sh . | cut -f1)"
ls -la
