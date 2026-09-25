#!/bin/bash
# Template: Authenticated Session Workflow
# Purpose: Login once, save state, reuse for subsequent runs
# Usage: ./authenticated-session.sh <login-url> <state-file>
#
# RECOMMENDED: Use the auth vault instead of this template:
#   echo "<pass>" | agent-browser auth save myapp --url <login-url> --username <user> --password-stdin
#   agent-browser auth login myapp
# The auth vault stores credentials securely and the LLM never sees passwords.
#
# Environment variables:
#   APP_USERNAME - Login username/email
#   APP_PASSWORD - Login password
#
# Two modes:
#   1. Discovery mode (default): Shows form structure so you can identify refs
#   2. Login mode: Performs actual login after you update the refs
#
# Setup steps:
#   1. Run once to see form structure (discovery mode)
#   2. Update refs in LOGIN FLOW section below
#   3. Set APP_USERNAME and APP_PASSWORD
#   4. Delete the DISCOVERY section

set -euo pipefail

LOGIN_URL="${1:?Usage: $0 <login-url> <state-file>}"
STATE_FILE="${2:?Usage: $0 <login-url> <state-file>}"
SESSION="authenticated-session-$$"
: "${AGENT_BROWSER_ALLOWED_DOMAINS:?Set AGENT_BROWSER_ALLOWED_DOMAINS to reviewed hosts}"
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

echo "Authentication workflow: $LOGIN_URL"

# ================================================================
# SAVED STATE: Skip login if valid saved state exists
# ================================================================
if [[ -f "$STATE_FILE" ]]; then
    chmod 600 "$STATE_FILE"
    echo "Loading saved state from $STATE_FILE..."
    if agent-browser --session "$SESSION" --state "$STATE_FILE" open "$LOGIN_URL" 2>/dev/null; then
        agent-browser --session "$SESSION" wait --load networkidle

        CURRENT_URL=$(agent-browser --session "$SESSION" get url)
        if [[ "$CURRENT_URL" != *"login"* ]] && [[ "$CURRENT_URL" != *"signin"* ]]; then
            echo "Session restored successfully"
            agent-browser --session "$SESSION" snapshot -i
            exit 0
        fi
        echo "Session expired, performing fresh login..."
        cleanup
    else
        echo "Failed to load state, re-authenticating..."
    fi
    echo "Preserving rejected state file for explicit cleanup: $STATE_FILE"
fi

# ================================================================
# DISCOVERY MODE: Shows form structure (delete after setup)
# ================================================================
echo "Opening login page..."
agent-browser --session "$SESSION" open "$LOGIN_URL"
agent-browser --session "$SESSION" wait --load networkidle

echo ""
echo "Login form structure:"
echo "---"
agent-browser --session "$SESSION" snapshot -i
echo "---"
echo ""
echo "Next steps:"
echo "  1. Note the refs: username=@e?, password=@e?, submit=@e?"
echo "  2. Update the LOGIN FLOW section below with your refs"
echo "  3. Set: export APP_USERNAME='...' APP_PASSWORD='...'"
echo "  4. Delete this DISCOVERY MODE section"
echo ""
cleanup
trap - EXIT HUP INT TERM
exit 0

# ================================================================
# LOGIN FLOW: Uncomment and customize after discovery
# ================================================================
# : "${APP_USERNAME:?Set APP_USERNAME environment variable}"
# : "${APP_PASSWORD:?Set APP_PASSWORD environment variable}"
#
# agent-browser --session "$SESSION" open "$LOGIN_URL"
# agent-browser --session "$SESSION" wait --load networkidle
# agent-browser --session "$SESSION" snapshot -i
#
# # Fill credentials (update refs to match your form)
# agent-browser --session "$SESSION" fill @e1 "$APP_USERNAME"
# agent-browser --session "$SESSION" fill @e2 "$APP_PASSWORD"
# agent-browser --session "$SESSION" click @e3
# agent-browser --session "$SESSION" wait --load networkidle
#
# # Verify login succeeded
# FINAL_URL=$(agent-browser --session "$SESSION" get url)
# if [[ "$FINAL_URL" == *"login"* ]] || [[ "$FINAL_URL" == *"signin"* ]]; then
#     echo "Login failed - still on login page"
#     agent-browser --session "$SESSION" screenshot "${STATE_FILE}.login-failed.png"
#     exit 1
# fi
#
# # Save state for future runs
# echo "Saving state to $STATE_FILE"
# agent-browser --session "$SESSION" state save "$STATE_FILE"
# chmod 600 "$STATE_FILE"
# echo "Login successful"
# agent-browser --session "$SESSION" snapshot -i
# cleanup
# trap - EXIT HUP INT TERM
