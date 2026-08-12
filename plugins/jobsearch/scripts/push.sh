#!/bin/bash
# ---------------------------------------------------------------------------
# push.sh — main-session push helper. Reads this session's push token and pushes.
#
# Do NOT invoke from a subagent. Subagents must never push (CLAUDE.md); the
# pre-push hook enforces it. This helper only works after the main session has
# minted the session token with scripts/push_init.sh.
#
# Usage:  scripts/push.sh            (pushes current branch)
#         scripts/push.sh -q         (extra args pass through to git push)
# ---------------------------------------------------------------------------
set -e
TOKEN_FILE="$(git rev-parse --git-dir)/push_token"
if [ ! -f "$TOKEN_FILE" ]; then
    echo "No session push token found." >&2
    echo "Run scripts/push_init.sh first (main session, at run start)." >&2
    exit 1
fi
CLAUDESEARCH_PUSH_TOKEN="$(cat "$TOKEN_FILE")" git push "$@"
