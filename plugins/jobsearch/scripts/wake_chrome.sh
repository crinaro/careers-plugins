#!/bin/bash
# Wakes the Claude-in-Chrome extension's MV3 service worker before the 7am/2pm
# cto-cio-daily scheduled task fires. Chrome.app running is not sufficient — the
# extension's connection to Claude drops when its background service worker is
# unloaded during idle periods, and only reconnects on renewed Chrome activity
# (see log.md 2026-07-13 for the diagnostic evidence). This forces a tab
# create/close cycle to trigger that reconnect ahead of time.
#
# Fired by a LaunchAgent at 06:58/13:58 — ~/Library/LaunchAgents/<label>.wake-chrome.plist,
# where <label> is whatever reverse-DNS label the installing user chose.
#
# --- 2026-07-20: two real bugs fixed, one of them serious -------------------
# 1. THE EXIT CODE WAS ALWAYS LOGGED AS 0, EVEN ON FAILURE. The old line was:
#        echo "$(date ...) wake-chrome done (exit $?)"
#    The $(date ...) command substitution runs during expansion and clobbers $?
#    BEFORE it is read, so the log said "exit 0" unconditionally. Proven:
#        bash -c 'false; echo "$(date +%s) (exit $?)"'        ->  (exit 0)
#        bash -c 'false; st=$?; echo "$(date +%s) (exit $st)"' -> (exit 1)
#    This mattered beyond cosmetics: those "exit 0" lines were cited on 7/19 and
#    again on 7/20 as machine-verified proof the job was healthy. The log could
#    not have reported a failure if one had occurred. Status is now captured
#    immediately into a variable, and osascript's own output is logged.
# 2. NO WINDOW = HARD FAILURE. `make new tab ... of front window` throws if
#    Chrome is running with zero windows (all closed, or a background-only
#    state). Now handled explicitly by opening a window instead.
#
# No Python dependency — pure bash + osascript, both on launchd's default PATH
# (/usr/bin:/bin:/usr/sbin:/sbin). Verified 2026-07-20, so this job is unaffected
# by the Homebrew-vs-system python3 PATH question that applies to the repo's
# Python scripts.

# --- 2026-08-04: --relaunch mode, per the candidate --------------------------
# "can we configure the chrome extension capabilities to quit and relaunch the
# browser if it runs into the issue" — the issue being navigate() timing out
# (300s) while Chrome looks alive: the MV3 service worker is wedged and a tab
# cycle does not always recover it. A full quit + reopen does, and Chrome
# restores the session, so nothing of the candidate's is lost. ESCALATION, not
# default: callers (linkedin-runner) try a plain wake first, then --relaunch
# ONCE, then report BROWSER UNAVAILABLE and queue the work.

LOGFILE="$HOME/.claude/scheduled-tasks/wake-chrome.log"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOGFILE"; }

log "wake-chrome fired${1:+ ($1)}"

if [ "${1:-}" = "--relaunch" ]; then
    log "relaunch requested: quitting Chrome"
    output=$(osascript 2>&1 -e 'tell application "Google Chrome" to quit')
    status=$?
    if [ "$status" -ne 0 ]; then
        log "relaunch FAILED at quit (exit $status) - $output"
        exit "$status"
    fi
    # Wait for the process to actually exit (quit is async); cap at 15s.
    for i in $(seq 1 15); do
        pgrep -x "Google Chrome" >/dev/null || break
        sleep 1
    done
    if pgrep -x "Google Chrome" >/dev/null; then
        log "relaunch FAILED: Chrome still running 15s after quit (unsaved-work dialog?)"
        exit 1
    fi
    open -a "Google Chrome"
    status=$?
    if [ "$status" -ne 0 ]; then
        log "relaunch FAILED at reopen (exit $status)"
        exit "$status"
    fi
    sleep 5   # let the extension service worker come up before the wake cycle below
    log "relaunch: Chrome reopened, proceeding to wake cycle"
fi

# 2>&1 so any AppleScript error text is captured rather than lost to stderr.
output=$(osascript 2>&1 <<'EOF'
tell application "Google Chrome"
    activate
    if (count of windows) is 0 then
        -- No window to attach a tab to. Opening one is itself enough activity
        -- to wake the service worker; leave it open rather than closing the
        -- only window we just created.
        make new window
        delay 2
        return "opened new window (none existed)"
    else
        set newTab to make new tab at end of tabs of front window with properties {URL:"chrome://new-tab-page/"}
        delay 2
        close newTab
        return "cycled a tab in existing window"
    end if
end tell
EOF
)
status=$?

if [ "$status" -eq 0 ]; then
    log "wake-chrome done (exit 0) - $output"
else
    # Loud and greppable: a silent failure here means the 7am run finds a dead
    # extension and skips the whole LinkedIn pass.
    log "wake-chrome FAILED (exit $status) - $output"
fi

exit "$status"
