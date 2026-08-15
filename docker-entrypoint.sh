#!/bin/sh
set -eu

# Bind-mounted directories are commonly created as root on the host. Fix only
# the dedicated persistence mount, then run migrations and the app unprivileged.
if [ "$(id -u)" = "0" ]; then
    mkdir -p /data
    chown -R fleet:fleet /data
    exec gosu fleet "$@"
fi

exec "$@"
