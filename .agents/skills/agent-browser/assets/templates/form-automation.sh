#!/bin/bash
# Template: Form Automation Workflow
# Purpose: Fill and submit web forms with validation
# Usage: ./form-automation.sh <form-url> [evidence-dir]
#
# This template demonstrates the snapshot-interact-verify pattern:
# 1. Navigate to form
# 2. Snapshot to get element refs
# 3. Fill fields using refs
# 4. Submit and verify result
#
# Customize: Update the refs (@e1, @e2, etc.) based on your form's snapshot output

set -euo pipefail

FORM_URL="${1:?Usage: $0 <form-url> [evidence-dir]}"
: "${AGENT_BROWSER_ALLOWED_DOMAINS:?Set AGENT_BROWSER_ALLOWED_DOMAINS to reviewed hosts}"
EVIDENCE_DIR="${2:-$(mktemp -d /tmp/agent-browser-form.XXXXXX)}"
SESSION="form-automation-$$"
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

mkdir -p "$EVIDENCE_DIR"

echo "Form automation: $FORM_URL"

# Step 1: Navigate to form
agent-browser --session "$SESSION" open "$FORM_URL"
agent-browser --session "$SESSION" wait --load networkidle

# Step 2: Snapshot to discover form elements
echo ""
echo "Form structure:"
agent-browser --session "$SESSION" snapshot -i

# Step 3: Fill form fields (customize these refs based on snapshot output)
#
# Common field types:
#   agent-browser --session "$SESSION" fill @e1 "John Doe"           # Text input
#   agent-browser --session "$SESSION" fill @e2 "user@example.com"   # Email input
#   agent-browser --session "$SESSION" select @e3 "Option Value"     # Dropdown
#   agent-browser --session "$SESSION" check @e4                      # Checkbox
#   agent-browser --session "$SESSION" click @e5                      # Radio button
#   agent-browser --session "$SESSION" fill @e6 "Multi-line text"    # Textarea
#   agent-browser --session "$SESSION" upload @e7 /path/to/file.pdf   # File upload
#
# Uncomment and modify:
# agent-browser --session "$SESSION" fill @e1 "Test User"
# agent-browser --session "$SESSION" fill @e2 "test@example.com"
# agent-browser --session "$SESSION" click @e3  # Submit only after reviewing its effect

# Step 4: Wait for submission
# agent-browser --session "$SESSION" wait --load networkidle
# agent-browser --session "$SESSION" wait --url "**/success"  # Or wait for redirect

# Step 5: Verify result
echo ""
echo "Result:"
agent-browser --session "$SESSION" get url
agent-browser --session "$SESSION" snapshot -i

# Optional: Capture evidence
agent-browser --session "$SESSION" screenshot "$EVIDENCE_DIR/form-result.png"
echo "Screenshot saved: $EVIDENCE_DIR/form-result.png"

# Cleanup
cleanup
trap - EXIT HUP INT TERM
echo "Done"
