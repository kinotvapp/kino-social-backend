#!/bin/sh
# Run as root inside WSL Ubuntu by start-all.ps1: PostgreSQL and Redis up, then stay attached so WSL never idles them away.
service postgresql start >/dev/null
service redis-server start >/dev/null
echo "kino-social services up"
exec sleep infinity
