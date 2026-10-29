#!/usr/bin/env bash
# Kino Social on Oracle Cloud: one entry point for the everyday jobs (run it from Git Bash in this folder).
#
#   bash scripts/oracle.sh ssh                 open a shell on the server
#   bash scripts/oracle.sh status              services, memory, disk, health
#   bash scripts/oracle.sh logs [api|caddy|tunnel|postgres|redis] [N]
#   bash scripts/oracle.sh deploy              upload this folder's code (app/, requirements.txt) and restart the API
#   bash scripts/oracle.sh restart [api|caddy|tunnel|all]
#   bash scripts/oracle.sh backup              download a database dump to backups/
#   bash scripts/oracle.sh restore FILE        load a dump INTO the server (replaces its database: asks first)
#   bash scripts/oracle.sh psql                SQL shell on the server's database
#   bash scripts/oracle.sh put LOCAL [REMOTE]  copy a file to the server (default /tmp/)
#   bash scripts/oracle.sh get REMOTE [LOCAL]  copy a file from the server
#
# Secrets never travel through here except data/pg.env and data/secret.key, and only on purpose (see ORACLE.md).
set -euo pipefail

HOST="${KS_HOST:-opc@129.158.250.81}"
KEY="${KS_KEY:-$HOME/.ssh/kino-social-oci}"
APP=/opt/kino-social/app-src
ENV="$APP/data/pg.env"
SSH=(ssh -i "$KEY" -o ServerAliveInterval=30 -o LogLevel=ERROR "$HOST")
SCP=(scp -i "$KEY" -o LogLevel=ERROR)
cd "$(dirname "$0")/.."

unit() {
  case "${1:-api}" in
    api) echo kino-social-api ;; caddy) echo caddy ;; tunnel) echo kino-social-tunnel ;;
    postgres) echo postgresql-18 ;; redis) echo redis ;; all) echo "kino-social-api caddy kino-social-tunnel" ;;
    *) echo "unknown service: $1" >&2; exit 2 ;;
  esac
}

# Runs on the server: the DB password from pg.env, never echoed.
REMOTE_PGPW='export PGPASSWORD=$(sudo cat '"$ENV"' | tr -d "\r" | sed -n "s#^KINO_SOCIAL_DSN=postgresql://kino_social:\([^@]*\)@.*#\1#p")'

case "${1:-}" in
  ssh)
    exec "${SSH[@]}" ;;
  status)
    "${SSH[@]}" 'systemctl is-active kino-social-api caddy kino-social-tunnel postgresql-18 redis | paste -sd" "
free -m | sed -n "1,3p"; df -h / | tail -1
curl -s -o /dev/null -w "API local %{http_code}\n" http://127.0.0.1:8787/health'
    curl -s -o /dev/null -w "HTTPS sslip.io %{http_code}\n" --max-time 20 https://129-158-250-81.sslip.io/health
    curl -s -o /dev/null -w "HTTPS old domain %{http_code}\n" --max-time 20 https://social.comparadorinternet.co/health ;;
  logs)
    "${SSH[@]}" "sudo journalctl -u $(unit "${2:-api}") -n ${3:-80} --no-pager" ;;
  restart)
    "${SSH[@]}" "sudo systemctl restart $(unit "${2:-api}") && systemctl is-active $(unit "${2:-api}") | paste -sd' '" ;;
  deploy)
    # Only code: never data/ (the server's secrets stay where they are), never .venv, logs or tests' caches.
    tar --exclude=.venv --exclude=logs --exclude=data --exclude=backups --exclude=__pycache__ --exclude=.pytest_cache \
        --exclude=.git -czf /tmp/ks-code.tgz app requirements.txt
    "${SCP[@]}" /tmp/ks-code.tgz "$HOST:/tmp/ks-code.tgz"; rm -f /tmp/ks-code.tgz
    "${SSH[@]}" "set -e
sudo cp -a $APP/app /opt/kino-social/app.previous 2>/dev/null || true
sudo tar -xzf /tmp/ks-code.tgz -C $APP && rm -f /tmp/ks-code.tgz && sudo chown -R kino:kino $APP
sudo -u kino /opt/kino-social/venv/bin/pip install -q -r $APP/requirements.txt
sudo systemctl restart kino-social-api; sleep 4
curl -sf http://127.0.0.1:8787/health >/dev/null && echo 'deployed: API healthy' || { echo 'API NOT healthy: rolling back'; sudo rm -rf $APP/app; sudo cp -a /opt/kino-social/app.previous $APP/app; sudo systemctl restart kino-social-api; exit 1; }" ;;
  backup)
    mkdir -p backups
    out="backups/kino_social-$(date +%Y%m%d-%H%M%S).dump"
    "${SSH[@]}" "$REMOTE_PGPW; pg_dump -Fc -h 127.0.0.1 -U kino_social kino_social" > "$out"
    echo "$out ($(wc -c < "$out") bytes)" ;;
  restore)
    f="${2:?usage: restore FILE.dump}"
    read -r -p "This REPLACES the server's database with $f. Type yes: " ok; [ "$ok" = yes ] || exit 1
    "${SCP[@]}" "$f" "$HOST:/tmp/ks.dump"
    "${SSH[@]}" "set -e; $REMOTE_PGPW
sudo systemctl stop kino-social-api
sudo -u postgres dropdb kino_social; sudo -u postgres createdb -O kino_social kino_social
pg_restore -h 127.0.0.1 -U kino_social -d kino_social --no-owner --role=kino_social /tmp/ks.dump; rm -f /tmp/ks.dump
sudo systemctl start kino-social-api; echo restored" ;;
  psql)
    exec "${SSH[@]}" -t "$REMOTE_PGPW; psql -h 127.0.0.1 -U kino_social kino_social" ;;
  put)
    "${SCP[@]}" "${2:?usage: put LOCAL [REMOTE]}" "$HOST:${3:-/tmp/}" ;;
  get)
    "${SCP[@]}" "$HOST:${2:?usage: get REMOTE [LOCAL]}" "${3:-.}" ;;
  *)
    sed -n '2,15p' "$0"; exit 2 ;;
esac
