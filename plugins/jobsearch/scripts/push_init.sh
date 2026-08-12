#!/bin/bash
# ---------------------------------------------------------------------------
# push_init.sh — mint THIS session's push token. MAIN SESSION ONLY, at run start.
#
# Writes a fresh random token to $GIT_DIR/push_token (inside .git/, so never
# tracked and never in the working tree a subagent browses). scripts/push.sh
# reads it; the pre-push hook validates it. Because the value is random and
# lives in no tracked doc, a subagent cannot authorize a push by reproducing a
# constant from CLAUDE.md — the failure mode this replaces.
#
# Idempotent-by-intent: re-running rotates the token (fine — the main session
# is the only writer/reader within a run).
# ---------------------------------------------------------------------------
set -e

# adr-012: a profile that DECLARES local-only has no push step, so no token is needed. Asking
# sync.py (the single owner of the question) keeps the run-start sequence identical in both
# modes — nothing conditional lives in a prompt. Fails OPEN: if the resolver cannot answer,
# mint the token as before rather than wedging an unattended run at step 0.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SYNC_JSON="$(python3 "$SCRIPT_DIR/sync.py" --json 2>/dev/null || true)"
if printf '%s' "$SYNC_JSON" | grep -q '"verdict": "ok"' && \
   printf '%s' "$SYNC_JSON" | grep -q '"mode": "local-only"'; then
    echo "No push token needed: this profile declares sync.mode: local-only (adr-012)."
    echo "End-of-run is commit only; sync.py --end-of-run prints the summary line."
    exit 0
fi

TOKEN_FILE="$(git rev-parse --git-dir)/push_token"
head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$TOKEN_FILE"
chmod 600 "$TOKEN_FILE"
echo "Session push token minted: $TOKEN_FILE"
echo "Push with: scripts/push.sh   (subagents must never push)"
