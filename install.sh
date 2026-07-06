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

APP_NAME="${APP_NAME:-Inventory Management System}"
DOMAIN="${DOMAIN:-}"
INSTALL_MODE="${INSTALL_MODE:-}"
START_STACK="${START_STACK:-true}"

require_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "Please run as root:"
    echo "  sudo sh install.sh"
    exit 1
  fi
}

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing command: $1"
    echo "Install $1 first, then run this script again."
    exit 1
  fi
}

need_docker_compose() {
  if ! docker compose version >/dev/null 2>&1; then
    echo "Docker Compose plugin is missing."
    echo "Install Docker Compose plugin, then run this script again."
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

prompt_mode() {
  if [ -n "$INSTALL_MODE" ]; then
    return
  fi

  echo
  echo "Choose how Inventory will be accessed:"
  echo "  1) MiniPC + Cloudflare Tunnel (recommended for current personal use)"
  echo "  2) Server/domain behind an existing reverse proxy"
  echo "  3) Server/domain direct on ports 80/443"
  printf "Select [1-3, default 1]: "
  read answer
  case "${answer:-1}" in
    1) INSTALL_MODE="tunnel" ;;
    2) INSTALL_MODE="proxy" ;;
    3) INSTALL_MODE="direct" ;;
    *) echo "Invalid choice"; exit 1 ;;
  esac
}

prompt_domain() {
  if [ -n "$DOMAIN" ]; then
    return
  fi

  case "$INSTALL_MODE" in
    tunnel|proxy|direct)
      printf "Domain/subdomain (optional, e.g. inventory.example.com): "
      read DOMAIN
      ;;
  esac
}

apply_access_mode_env() {
  case "$INSTALL_MODE" in
    tunnel)
      replace_env INVENTORY_HTTP_BIND "127.0.0.1"
      replace_env INVENTORY_HTTP_PORT "8088"
      replace_env INVENTORY_HTTPS_BIND "127.0.0.1"
      replace_env INVENTORY_HTTPS_PORT "8443"
      replace_env PREFERRED_URL_SCHEME "https"
      replace_env FORCE_HTTPS "false"
      replace_env SESSION_COOKIE_SECURE "true"
      ;;
    proxy)
      replace_env INVENTORY_HTTP_BIND "127.0.0.1"
      replace_env INVENTORY_HTTP_PORT "8088"
      replace_env INVENTORY_HTTPS_BIND "127.0.0.1"
      replace_env INVENTORY_HTTPS_PORT "8443"
      replace_env PREFERRED_URL_SCHEME "https"
      replace_env FORCE_HTTPS "false"
      replace_env SESSION_COOKIE_SECURE "true"
      ;;
    direct)
      replace_env INVENTORY_HTTP_BIND "0.0.0.0"
      replace_env INVENTORY_HTTP_PORT "80"
      replace_env INVENTORY_HTTPS_BIND "0.0.0.0"
      replace_env INVENTORY_HTTPS_PORT "443"
      replace_env PREFERRED_URL_SCHEME "https"
      replace_env FORCE_HTTPS "false"
      replace_env SESSION_COOKIE_SECURE "true"
      ;;
    *)
      echo "Unknown INSTALL_MODE: $INSTALL_MODE"
      echo "Use one of: tunnel, proxy, direct"
      exit 1
      ;;
  esac

  replace_env INVENTORY_ACCESS_MODE "$INSTALL_MODE"
  replace_env INVENTORY_DOMAIN "$DOMAIN"
}

create_directories() {
  mkdir -p "$APP_DIR" "$COMPOSE_DIR" "$CONFIG_DIR/ssl" "$SCRIPT_DIR" "$SECRET_DIR" "$BACKUP_DIR"
  mkdir -p /srv/inventory/data/instance /srv/inventory/data/backups /srv/inventory/data/logs
  mkdir -p /srv/postgres/inventory/data /srv/redis/inventory/data
}

sync_source() {
  if [ -d "$APP_DIR/.git" ]; then
    echo "Updating Inventory source at $APP_DIR"
    git -C "$APP_DIR" pull --ff-only
    return
  fi

  if [ -n "$(find "$APP_DIR" -mindepth 1 -maxdepth 1 2>/dev/null | head -n 1)" ]; then
    echo "$APP_DIR is not empty and is not a git repository."
    echo "Move its contents elsewhere, then run this script again."
    exit 1
  fi

  echo "Cloning Inventory from $REPO_URL"
  git clone "$REPO_URL" "$APP_DIR"
}

install_files() {
  cp "$APP_DIR/docker-compose.yml" "$COMPOSE_DIR/compose.yaml"
  cp "$APP_DIR/nginx.conf" "$CONFIG_DIR/nginx.conf"
  cp "$APP_DIR/init-ssl.sh" "$SCRIPT_DIR/init-ssl.sh"
  chmod +x "$SCRIPT_DIR/init-ssl.sh"
}

create_or_update_env() {
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

  replace_env APP_NAME "$APP_NAME"
  replace_env INVENTORY_APP_DIR "$APP_DIR"
  replace_env INVENTORY_DATA_DIR "/srv/inventory/data"
  replace_env POSTGRES_DATA_DIR "/srv/postgres/inventory/data"
  replace_env REDIS_DATA_DIR "/srv/redis/inventory/data"
  replace_env INVENTORY_NGINX_CONF "$CONFIG_DIR/nginx.conf"
  replace_env INVENTORY_SSL_DIR "$CONFIG_DIR/ssl"
  replace_env INVENTORY_INIT_SSL "$SCRIPT_DIR/init-ssl.sh"
  replace_env INVENTORY_ENV_FILE "$ENV_FILE"
  apply_access_mode_env
}

start_stack() {
  if [ "$START_STACK" != "true" ]; then
    echo "START_STACK is not true, skipping Docker Compose start."
    return
  fi

  cd "$COMPOSE_DIR"
  docker compose --env-file "$ENV_FILE" up -d --build
}

print_summary() {
  echo
  echo "Inventory install/update completed."
  echo
  echo "Mode: $INSTALL_MODE"
  if [ -n "$DOMAIN" ]; then
    echo "Domain: $DOMAIN"
  fi
  echo "Source: $APP_DIR"
  echo "Compose: $COMPOSE_DIR/compose.yaml"
  echo "Env: $ENV_FILE"
  echo "Data: /srv/inventory/data"
  echo "PostgreSQL: /srv/postgres/inventory/data"
  echo

  case "$INSTALL_MODE" in
    tunnel)
      echo "Cloudflare Tunnel target:"
      echo "  http://localhost:8088"
      ;;
    proxy)
      echo "Reverse proxy upstream:"
      echo "  http://127.0.0.1:8088"
      ;;
    direct)
      echo "Direct access:"
      echo "  http://SERVER_IP/"
      echo "  https://SERVER_IP/"
      echo "Replace self-signed certs in $CONFIG_DIR/ssl when using a real domain."
      ;;
  esac

  echo
  echo "First admin setup:"
  echo "  Open /setup through your domain/subdomain, or local URL:"
  echo "  http://127.0.0.1:8088/setup"
  echo
  echo "Useful commands:"
  echo "  cd $COMPOSE_DIR"
  echo "  docker compose --env-file $ENV_FILE ps"
  echo "  docker compose --env-file $ENV_FILE logs -f inventory-app"
}

main() {
  require_root
  need_cmd git
  need_cmd docker
  need_docker_compose
  prompt_mode
  prompt_domain
  create_directories
  sync_source
  install_files
  create_or_update_env
  start_stack
  print_summary
}

main "$@"
