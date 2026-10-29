#!/bin/sh
# Run as root inside WSL Ubuntu: Redis on 127.0.0.1 only, with the password from data/pg.env (KINO_SOCIAL_REDIS).
set -e
ENV=/mnt/c/dev/kino-social-backend/data/pg.env
P=$(tr -d '\r' < "$ENV" | sed -n 's#^KINO_SOCIAL_REDIS=redis://:\([^@]*\)@.*#\1#p')
[ -n "$P" ] || { echo "no KINO_SOCIAL_REDIS in $ENV"; exit 1; }
C=/etc/redis/redis.conf
sed -i '/^requirepass /d; /^# *requirepass /d' "$C"
sed -i 's/^bind .*/bind 127.0.0.1 -::1/' "$C"
echo "requirepass $P" >> "$C"
service redis-server restart >/dev/null
sleep 1
redis-cli -a "$P" --no-auth-warning ping
