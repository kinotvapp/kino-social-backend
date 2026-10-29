#!/bin/sh
# Run as root inside WSL Ubuntu: starts PostgreSQL and makes sure both databases exist, owned by kino_social.
set -e
service postgresql start >/dev/null
for d in kino_social kino_social_test; do
  if ! su postgres -c "psql -tAc \"SELECT 1 FROM pg_database WHERE datname = '$d'\"" | grep -q 1; then
    su postgres -c "createdb -O kino_social $d"
  fi
done
su postgres -c "psql -tAc \"SELECT datname FROM pg_database WHERE datname LIKE 'kino%'\""
