#!/usr/bin/env bash
# Regenerates the project-root .env from podman secrets. Podman secrets are the canonical
# store for this token (matching every other site in this workspace -- see
# website/README.md's "Secrets & tokens" section); .env is a generated, 0600, gitignored
# local artifact that worldcup_predictor.config's load_dotenv() reads, so every existing
# entrypoint (crontab.example's cron lines, the two systemd services, the sched-*.sh loops,
# local dev) keeps working with zero changes.
#
# Run this once after first creating a secret, and again any time you rotate one:
#   printf '%s' "$NEW_TOKEN" | podman secret create worldcup-football-data-token -
#   deploy/sync-env-from-secrets.sh
set -euo pipefail
cd "$(dirname "$0")/.."

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
chmod 600 "$TMP"

_write() {
  local secret_name="$1" env_name="$2"
  if podman secret exists "$secret_name" 2>/dev/null; then
    printf '%s=%s\n' "$env_name" "$(podman secret inspect --showsecret --format '{{.SecretData}}' "$secret_name")" >> "$TMP"
  fi
}

_write worldcup-football-data-token FOOTBALL_DATA_TOKEN
_write worldcup-odds-api-key        ODDS_API_KEY
_write worldcup-newsapi-key         NEWSAPI_KEY   # optional, see .env.example

count=$(wc -l < "$TMP")
mv "$TMP" .env
echo "Regenerated .env from $count podman secret(s)."
