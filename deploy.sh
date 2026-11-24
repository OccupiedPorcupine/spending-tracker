#!/usr/bin/env bash
# Ship a commit to mysites, rebuild, migrate, restart, check health, and (re)load the Caddy site.
#
#   ./deploy.sh              deploy HEAD
#   ./deploy.sh <commit>     deploy (or roll back to) any commit, tag or branch
#
# The default target is mysites' LAN address, which the Mac reaches through the tailnet's subnet
# route. `root@mysites` (Tailscale SSH) asks for a browser re-check, which a script cannot answer.
# Override with DEPLOY_HOST=root@mysites ./deploy.sh if that check is turned off.
set -euo pipefail
cd "$(dirname "$0")"

HOST="${DEPLOY_HOST:-root@10.10.10.102}"
DIR="${DEPLOY_DIR:-/home/maize/spend}"
SITES="/home/maize/caddy-sites"
CADDY="quant-gym-caddy-1"
REF="${1:-HEAD}"
COMMIT="$(git rev-parse --short "$REF")"
SSH=(ssh -o ConnectTimeout=10 -o BatchMode=yes "$HOST")

if [ "$REF" = "HEAD" ] && [ -n "$(git status --porcelain)" ]; then
  echo "note: uncommitted changes are NOT deployed (shipping $COMMIT)"
fi

echo "==> $COMMIT -> $HOST:$DIR"
"${SSH[@]}" "test -s '$DIR/.env'" || { echo "missing $DIR/.env on the server (copy .env.example and fill it in)"; exit 1; }
"${SSH[@]}" "rm -rf '$DIR/src.new' && mkdir -p '$DIR/backups' '$DIR/src.new' '$DIR/secrets' && chmod 700 '$DIR/secrets' && chmod 600 '$DIR/.env'"
git archive --format=tar "$REF" | "${SSH[@]}" "tar -x -C '$DIR/src.new'"

"${SSH[@]}" DIR="$DIR" COMMIT="$COMMIT" SITES="$SITES" CADDY="$CADDY" bash -s <<'REMOTE'
set -euo pipefail
cd "$DIR"
SA="$(grep -E '^GOOGLE_SA_FILE_HOST=' .env | cut -d= -f2-)"; SA="${SA:-$DIR/secrets/google-sa.json}"
if [ ! -s "$SA" ]; then
  # A missing bind-mount source would be created as a directory; keep it a (still empty) file.
  touch "$SA"; chmod 600 "$SA"
  echo "WARNING: $SA is empty: sheet reads and writes will fail until the service account key is there"
fi
rm -rf src.old && { [ -d src ] && mv src src.old || true; } && mv src.new src
chown -R maize:maize "$DIR"
cd src
export GIT_COMMIT="$COMMIT"
C=(docker compose --env-file "$DIR/.env")

docker image inspect spend-app:latest >/dev/null 2>&1 && docker tag spend-app:latest spend-app:prev || true

echo "==> build"
"${C[@]}" build --quiet

echo "==> migrate"
# </dev/null: this script arrives on stdin, and `run` would otherwise swallow the rest of it
"${C[@]}" run --rm --no-deps -T app python -m app.migrate </dev/null

echo "==> restart"
"${C[@]}" up -d --remove-orphans

PORT="$(grep -E '^APP_PORT=' "$DIR/.env" | cut -d= -f2 || true)"; PORT="${PORT:-8001}"
echo "==> health"
for i in $(seq 1 30); do
  if out="$(curl -fsS -m 3 "http://127.0.0.1:$PORT/api/health" 2>/dev/null)"; then echo "$out"; ok=1; break; fi
  sleep 1
done
[ "${ok:-}" = 1 ] || { echo "health check FAILED"; "${C[@]}" logs --tail=40 app; exit 1; }
echo "$COMMIT $(date -u +%FT%TZ)" >> "$DIR/deploys.log"

echo "==> caddy site"
if ! docker exec "$CADDY" grep -q '^import /etc/caddy/sites/' /etc/caddy/Caddyfile 2>/dev/null; then
  echo "WARNING: $CADDY does not import $SITES yet (gym's Caddyfile change); skipping"
else
  mkdir -p "$SITES"
  NEW="$(sed "s/__APP_PORT__/$PORT/" deploy/caddy/spend.caddy)"
  if [ "$NEW" != "$(cat "$SITES/spend.caddy" 2>/dev/null)" ]; then
    [ -f "$SITES/spend.caddy" ] && cp "$SITES/spend.caddy" "$SITES/spend.caddy.bak-$(date +%Y%m%d-%H%M%S)"
    printf '%s\n' "$NEW" > "$SITES/spend.caddy.tmp" && mv "$SITES/spend.caddy.tmp" "$SITES/spend.caddy"
    if docker exec "$CADDY" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
      docker exec "$CADDY" caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
      echo "caddy reloaded with the new spend.caddy"
    else
      echo "caddy validate FAILED; restoring the previous spend.caddy"
      docker exec "$CADDY" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile 2>&1 | tail -5 || true
      last="$(ls -t "$SITES"/spend.caddy.bak-* 2>/dev/null | head -1 || true)"
      if [ -n "$last" ]; then cp "$last" "$SITES/spend.caddy"; else rm -f "$SITES/spend.caddy"; fi
      exit 1
    fi
  else
    echo "spend.caddy unchanged"
  fi
fi

echo "==> backup timer"
for u in spend-backup.service spend-backup.timer; do
  cmp -s "deploy/systemd/$u" "/etc/systemd/system/$u" || { cp "deploy/systemd/$u" "/etc/systemd/system/$u"; changed=1; }
done
[ "${changed:-}" = 1 ] && systemctl daemon-reload
systemctl enable --now spend-backup.timer >/dev/null 2>&1
systemctl list-timers spend-backup.timer --no-legend --no-pager | awk '{print "next backup:", $1, $2, $3}'

echo "==> other sites on this box"
printf 'quant-gym (127.0.0.1:8000): %s\n' "$(curl -fsS -m 5 http://127.0.0.1:8000/api/health >/dev/null && echo ok || echo FAILED)"
printf 'oracle-site (127.0.0.1:3000): HTTP %s\n' "$(curl -s -o /dev/null -m 8 -w '%{http_code}' http://127.0.0.1:3000/ || echo unreachable)"
docker ps --format '{{.Names}}\t{{.Status}}'
REMOTE
echo "==> deployed $COMMIT"
