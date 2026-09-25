#!/bin/bash
# Template: Content Capture Workflow
# Purpose: Extract content from web pages (text, screenshots, PDF)
# Usage: ./capture-workflow.sh <url> [output-dir]
#
# Outputs:
#   - page-full.png: Full page screenshot
#   - page-structure.txt: Page element structure with refs
#   - page-text.txt: All text content
#   - page.pdf: PDF version
#
# Optional: Load auth state for protected pages

set -euo pipefail

TARGET_URL="${1:?Usage: $0 <url> [output-dir]}"
: "${AGENT_BROWSER_ALLOWED_DOMAINS:?Set AGENT_BROWSER_ALLOWED_DOMAINS to reviewed hosts}"
OUTPUT_DIR="${2:-$(mktemp -d /tmp/agent-browser-capture.XXXXXX)}"
SESSION="capture-workflow-$$"
export AGENT_BROWSER_ALLOWED_DOMAINS
export AGENT_BROWSER_CONTENT_BOUNDARIES="${AGENT_BROWSER_CONTENT_BOUNDARIES:-1}"
export AGENT_BROWSER_MAX_OUTPUT="${AGENT_BROWSER_MAX_OUTPUT:-20000}"

cleanup() {
    agent-browser --session "$SESSION" close >/dev/null 2>&1 || true
}
abort() {
    code="$1"
    trap - EXIT
    cleanup
    exit "$code"
}
trap cleanup EXIT
trap 'abort 129' HUP
trap 'abort 130' INT
trap 'abort 143' TERM

echo "Capturing: $TARGET_URL"
mkdir -p "$OUTPUT_DIR"

# Optional: Load authentication state
# if [[ -f "./auth-state.json" ]]; then
#     echo "Loading authentication state..."
#     agent-browser --session "$SESSION" state load "./auth-state.json"
# fi

# Navigate to target
agent-browser --session "$SESSION" open "$TARGET_URL"
agent-browser --session "$SESSION" wait --load networkidle

# Get metadata
TITLE=$(agent-browser --session "$SESSION" get title)
URL=$(agent-browser --session "$SESSION" get url)
echo "Title: $TITLE"
echo "URL: $URL"

# Capture full page screenshot
agent-browser --session "$SESSION" screenshot --full "$OUTPUT_DIR/page-full.png"
echo "Saved: $OUTPUT_DIR/page-full.png"

# Get page structure with refs
agent-browser --session "$SESSION" snapshot -i > "$OUTPUT_DIR/page-structure.txt"
echo "Saved: $OUTPUT_DIR/page-structure.txt"

# Extract all text content
agent-browser --session "$SESSION" get text body > "$OUTPUT_DIR/page-text.txt"
echo "Saved: $OUTPUT_DIR/page-text.txt"

# Save as PDF
agent-browser --session "$SESSION" pdf "$OUTPUT_DIR/page.pdf"
echo "Saved: $OUTPUT_DIR/page.pdf"

# Optional: Extract specific elements using refs from structure
# agent-browser --session "$SESSION" get text @e5 > "$OUTPUT_DIR/main-content.txt"

# Optional: Handle infinite scroll pages
# for i in {1..5}; do
#     agent-browser --session "$SESSION" scroll down 1000
#     agent-browser --session "$SESSION" wait 1000
# done
# agent-browser --session "$SESSION" screenshot --full "$OUTPUT_DIR/page-scrolled.png"

# Cleanup
cleanup
trap - EXIT HUP INT TERM

echo ""
echo "Capture complete:"
ls -la "$OUTPUT_DIR"
