# Inventory homelab/server deployment

Tai lieu nay dua app Inventory vao dung mo hinh:

- Docker/compose/config/secrets nam trong `/opt/docker`
- Du lieu runtime nam trong `/srv`
- Database va Redis tach khoi code de backup/bao tri de hon
- Co the chay qua domain rieng, Cloudflare Tunnel, hoac LAN noi bo

## 1. Cau truc thu muc tren server

```text
/opt/docker
  compose/
  scripts/
  backup/
  configs/
    inventory/
      ssl/
  secrets/

/srv
  inventory/
    app/
    instance/
    logs/
    backups/
  postgres/
    inventory/
  redis/
    inventory/
  immich/
```

## 2. Dua code len server

Dat code app vao:

```bash
/srv/inventory/app
```

Vi du:

```bash
sudo mkdir -p /srv/inventory/app /srv/inventory/instance /srv/inventory/logs /srv/inventory/backups
sudo mkdir -p /srv/postgres/inventory /srv/redis/inventory
sudo mkdir -p /opt/docker/compose /opt/docker/configs/inventory/ssl /opt/docker/scripts /opt/docker/secrets
```

Copy cac file:

```bash
cp /srv/inventory/app/docker-compose.yml /opt/docker/compose/inventory.yml
cp /srv/inventory/app/nginx.conf /opt/docker/configs/inventory/nginx.conf
cp /srv/inventory/app/init-ssl.sh /opt/docker/scripts/inventory-init-ssl.sh
cp /srv/inventory/app/production.env /opt/docker/secrets/inventory.env
chmod 600 /opt/docker/secrets/inventory.env
chmod +x /opt/docker/scripts/inventory-init-ssl.sh
```

Sua `/opt/docker/secrets/inventory.env` va doi toi thieu:

- `SECRET_KEY`
- `POSTGRES_PASSWORD`
- Mat khau trong `DATABASE_URL`
- `ADMIN_PASSWORD`

Gia tri `POSTGRES_PASSWORD` va mat khau trong `DATABASE_URL` phai giong nhau.

## 3. Chay bang Docker Compose

```bash
cd /opt/docker/compose
INVENTORY_ENV_FILE=/opt/docker/secrets/inventory.env docker compose --env-file /opt/docker/secrets/inventory.env -f inventory.yml up -d --build
```

Kiem tra:

```bash
docker compose --env-file /opt/docker/secrets/inventory.env -f inventory.yml ps
curl http://127.0.0.1:8088/health
```

Lan dau container len, app se tu:

- Doi Postgres san sang
- Tao bang database
- Tao tai khoan `admin` bang `ADMIN_PASSWORD`
- Seed role/permission co ban

## 4. Chay qua Cloudflare Tunnel

De an app chi sau tunnel, giu trong env:

```env
INVENTORY_HTTP_BIND=127.0.0.1
INVENTORY_HTTP_PORT=8088
PREFERRED_URL_SCHEME=https
FORCE_HTTPS=false
SESSION_COOKIE_SECURE=true
```

Trong Cloudflare Tunnel, tro subdomain ve:

```text
http://localhost:8088
```

Neu tunnel chay trong container rieng, co the tro ve IP host hoac dua cloudflared vao cung Docker network, tuy cach ban dang quan ly Immich.

## 5. Chay LAN noi bo

Neu muon may khac trong mang noi bo truy cap truc tiep:

```env
INVENTORY_HTTP_BIND=0.0.0.0
INVENTORY_HTTP_PORT=8088
PREFERRED_URL_SCHEME=http
FORCE_HTTPS=false
SESSION_COOKIE_SECURE=false
```

Sau do mo:

```text
http://IP_SERVER:8088
```

## 6. Chay domain rieng co reverse proxy ngoai

Neu server da co Nginx Proxy Manager, Caddy, Traefik, hoac reverse proxy chung:

- Giu Inventory bind local: `127.0.0.1:8088`
- Reverse proxy domain ve `http://127.0.0.1:8088`
- De TLS/Let's Encrypt o reverse proxy ngoai
- Giu `PREFERRED_URL_SCHEME=https`
- Giu `SESSION_COOKIE_SECURE=true`

Chi bat `FORCE_HTTPS=true` khi reverse proxy da gui dung header `X-Forwarded-Proto`.

## 7. Backup

Du lieu can backup:

```text
/srv/inventory/instance
/srv/inventory/backups
/srv/postgres/inventory
/opt/docker/secrets/inventory.env
/opt/docker/configs/inventory
```

Backup Postgres nen dung dump logic:

```bash
docker compose --env-file /opt/docker/secrets/inventory.env -f /opt/docker/compose/inventory.yml exec app python backup_restore.py backup /app/backups/manual_$(date +%Y%m%d_%H%M%S).zip
```

## 8. Cap nhat app

```bash
cd /srv/inventory/app
git pull
cd /opt/docker/compose
INVENTORY_ENV_FILE=/opt/docker/secrets/inventory.env docker compose --env-file /opt/docker/secrets/inventory.env -f inventory.yml up -d --build
```
