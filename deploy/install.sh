#!/usr/bin/env bash
# Installs this checkout as systemd services on a Linux server: tick every 5 minutes + the web report.
# Run from anywhere; paths and the service user come from the checkout location and sudo.
#
#   sudo deploy/install.sh                 # install, enable, start (WITH_WEB=0 to skip the web server)
#   sudo deploy/install.sh --uninstall     # stop and remove the units (code, .env, mcr.db stay)
#   deploy/install.sh --render /tmp/units  # only write the filled-in units there, to review
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="${RUN_USER:-${SUDO_USER:-$(id -un)}}"
WITH_WEB="${WITH_WEB:-1}"
DEST=/etc/systemd/system
UNITS=(mcr-tick.service mcr-tick.timer)
[[ "$WITH_WEB" == 1 ]] && UNITS+=(mcr-web.service)

render() {  # $1 = output dir
    for u in "${UNITS[@]}"; do
        sed -e "s|@REPO@|$REPO|g" -e "s|@USER@|$RUN_USER|g" "$REPO/deploy/systemd/$u" > "$1/$u"
    done
}

case "${1:-}" in
    --uninstall)
        systemctl disable --now mcr-tick.timer mcr-web.service 2>/dev/null || true
        rm -f "$DEST"/mcr-tick.service "$DEST"/mcr-tick.timer "$DEST"/mcr-web.service
        systemctl daemon-reload
        echo "removed mcr units (code, .env, mcr.db untouched)"
        exit 0 ;;
    --render)
        mkdir -p "${2:?usage: --render DIR}"
        render "$2"
        echo "rendered ${UNITS[*]} -> $2 (user $RUN_USER, repo $REPO)"
        exit 0 ;;
    "") ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
esac

[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
[[ "$RUN_USER" != root ]] || { echo "refusing to run the jobs as root: sudo from the service user or set RUN_USER" >&2; exit 1; }
[[ -x "$REPO/.venv/bin/python" ]] || {
    echo "venv missing: cd $REPO && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2; exit 1; }
if [[ -f "$REPO/.env" ]]; then
    [[ "$(stat -c %a "$REPO/.env")" == 600 ]] || echo "warning: chmod 600 $REPO/.env (it holds API keys)" >&2
else
    echo "warning: $REPO/.env not found: KIS and Telegram are disabled until it exists" >&2
fi

render "$DEST"
systemctl daemon-reload
systemctl enable --now mcr-tick.timer
[[ "$WITH_WEB" == 1 ]] && systemctl enable --now mcr-web.service
systemctl --no-pager list-timers mcr-tick.timer
echo "installed: ${UNITS[*]} (user $RUN_USER, repo $REPO)"
