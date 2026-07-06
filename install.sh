#!/bin/sh
set -eu

REPO_URL="${REPO_URL:-https://github.com/avhthang/inventory-management.git}"
APP_DIR="${INVENTORY_APP_DIR:-/srv/inventory/app}"
COMPOSE_DIR="${INVENTORY_COMPOSE_DIR:-/opt/docker/compose/inventory}"
CONFIG_DIR="${INVENTORY_CONFIG_DIR:-/opt/docker/configs/inventory}"
SCRIPT_DIR="${INVENTORY_SCRIPT_DIR:-/opt/docker/scripts/inventory}"
SECRET_DIR="${INVENTORY_SECRET_DIR:-/opt/docker/secrets/inventory}"
BACKUP_DIR="${INVENTORY_SYSTEM_BACKUP_DIR:-/opt/docker/backup/inventory}"
ENV_FILE="$SECRET_DIR/inventory.env"

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run as root: sudo sh install.sh"
  exit 1
fi

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing command: $1"
    echo "Install it first, then run this script again."
    exit 1
  fi
}

random_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 36 | tr -d '\n'
  else
    date +%s%N | sha256sum | awk '{print $1}'
  fi
}

replace_env() {
  key="$1"
  value="$2"
  if grep -q "^$key=" "$ENV_FILE"; then
    sed -i "s|^$key=.*|$key=$value|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

need_cmd git
need_cmd docker

mkdir -p "$APP_DIR" "$COMPOSE_DIR" "$CONFIG_DIR/ssl" "$SCRIPT_DIR" "$SECRET_DIR" "$BACKUP_DIR"
mkdir -p /srv/inventory/data/instance /srv/inventory/data/backups /srv/inventory/data/logs
mkdir -p /srv/postgres/inventory/data /srv/redis/inventory/data

if [ -d "$APP_DIR/.git" ]; then
  echo "Updating Inventory source at $APP_DIR"
  git -C "$APP_DIR" pull --ff-only
else
  if [ -n "$(find "$APP_DIR" -mindepth 1 -maxdepth 1 2>/dev/null | head -n 1)" ]; then
    echo "$APP_DIR is not empty and is not a git repository."
    echo "Move its contents elsewhere, then run this script again."
    exit 1
  fi
  echo "Cloning Inventory from $REPO_URL"
  git clone "$REPO_URL" "$APP_DIR"
fi

cp "$APP_DIR/docker-compose.yml" "$COMPOSE_DIR/compose.yaml"
cp "$APP_DIR/nginx.conf" "$CONFIG_DIR/nginx.conf"
cp "$APP_DIR/init-ssl.sh" "$SCRIPT_DIR/init-ssl.sh"
chmod +x "$SCRIPT_DIR/init-ssl.sh"

if [ ! -f "$ENV_FILE" ]; then
  cp "$APP_DIR/production.env" "$ENV_FILE"
  db_password="$(random_secret)"
  replace_env SECRET_KEY "$(random_secret)"
  replace_env POSTGRES_PASSWORD "$db_password"
  replace_env DATABASE_URL "postgresql://inventory_user:$db_password@inventory-postgres:5432/inventory_db"
  replace_env INVENTORY_CREATE_ADMIN_FROM_ENV "false"
  replace_env ADMIN_PASSWORD ""
  chmod 600 "$ENV_FILE"
  echo "Created $ENV_FILE"
else
  echo "Keeping existing $ENV_FILE"
fi

cd "$COMPOSE_DIR"
docker compose --env-file "$ENV_FILE" up -d --build

echo
echo "Inventory is starting."
echo "Open the first-run setup page through your tunnel/domain, or locally:"
echo "  http://127.0.0.1:8088/setup"
echo
echo "Useful commands:"
echo "  cd $COMPOSE_DIR && docker compose --env-file $ENV_FILE ps"
echo "  cd $COMPOSE_DIR && docker compose --env-file $ENV_FILE logs -f inventory-app"
