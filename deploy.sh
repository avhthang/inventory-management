#!/bin/sh
set -eu

cat <<'EOF'
Inventory now uses the isolated Docker homelab layout.

Use this structure on the server:

  /opt/docker/compose/inventory/compose.yaml
  /opt/docker/configs/inventory/
  /opt/docker/scripts/inventory/
  /opt/docker/secrets/inventory/inventory.env
  /srv/inventory/
  /srv/postgres/inventory/data
  /srv/redis/inventory/data

Start or update Inventory with:

  cd /opt/docker/compose/inventory
  docker compose --env-file /opt/docker/secrets/inventory/inventory.env up -d --build

Read HOMELAB_DEPLOYMENT.md for the full setup guide.
EOF
