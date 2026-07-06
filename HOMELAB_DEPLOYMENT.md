# Inventory homelab/server deployment

Tai lieu nay quy hoach Inventory de khong lan voi Immich hoac cac dich vu khac.
Moi dich vu co compose/config/secrets/scripts rieng trong `/opt/docker`, va du lieu runtime rieng trong `/srv`.

## 1. Quy uoc tong the

```text
/opt/docker
  compose/
    immich/
      compose.yaml
    inventory/
      compose.yaml
  configs/
    immich/
    inventory/
      nginx.conf
      ssl/
  scripts/
    immich/
    inventory/
      init-ssl.sh
  secrets/
    immich/
    inventory/
      inventory.env
  backup/
    immich/
    inventory/

/srv
  immich/
    config/
  inventory/
    app/
    data/
      instance/
      backups/
      logs/
  postgres/
    immich/
    inventory/
      data/
  redis/
    immich/
    inventory/
      data/
```

Nguyen tac:

- `/opt/docker/compose/<service>/compose.yaml`: file compose cua tung service.
- `/opt/docker/configs/<service>`: config reverse proxy/nginx/ssl cua service.
- `/opt/docker/secrets/<service>`: env va secret, khong de trong code.
- `/opt/docker/scripts/<service>`: script phu tro rieng cua service.
- `/opt/docker/backup/<service>`: noi dat job/ket qua backup cap he thong neu can.
- `/srv/<service>`: du lieu rieng cua app.
- `/srv/postgres/<service>` va `/srv/redis/<service>`: database/cache tach rieng theo service.

## 2. Tao thu muc cho Inventory

```bash
sudo mkdir -p /opt/docker/compose/inventory
sudo mkdir -p /opt/docker/configs/inventory/ssl
sudo mkdir -p /opt/docker/scripts/inventory
sudo mkdir -p /opt/docker/secrets/inventory
sudo mkdir -p /opt/docker/backup/inventory

sudo mkdir -p /srv/inventory/app
sudo mkdir -p /srv/inventory/data/instance
sudo mkdir -p /srv/inventory/data/backups
sudo mkdir -p /srv/inventory/data/logs
sudo mkdir -p /srv/postgres/inventory/data
sudo mkdir -p /srv/redis/inventory/data
```

Dat source code Inventory tai:

```text
/srv/inventory/app
```

Co the cai nhanh bang script:

```bash
git clone https://github.com/avhthang/inventory-management.git /tmp/inventory-management
sudo sh /tmp/inventory-management/install.sh
```

Script se:

- Tao cau truc `/opt/docker/compose/inventory`, `/opt/docker/configs/inventory`, `/opt/docker/secrets/inventory`, `/srv/inventory`, `/srv/postgres/inventory`, `/srv/redis/inventory`
- Clone hoac update source tu GitHub vao `/srv/inventory/app`
- Tao `/opt/docker/secrets/inventory/inventory.env` neu chua co
- Sinh `SECRET_KEY` va mat khau PostgreSQL ngau nhien
- Start stack bang Docker Compose

Neu muon lam thu cong, tiep tuc cac buoc ben duoi.

## 3. Copy file trien khai

Tu source code Inventory:

```bash
cp /srv/inventory/app/docker-compose.yml /opt/docker/compose/inventory/compose.yaml
cp /srv/inventory/app/nginx.conf /opt/docker/configs/inventory/nginx.conf
cp /srv/inventory/app/init-ssl.sh /opt/docker/scripts/inventory/init-ssl.sh
cp /srv/inventory/app/production.env /opt/docker/secrets/inventory/inventory.env

chmod 600 /opt/docker/secrets/inventory/inventory.env
chmod +x /opt/docker/scripts/inventory/init-ssl.sh
```

Sua `/opt/docker/secrets/inventory/inventory.env` va doi toi thieu:

- `SECRET_KEY`
- `POSTGRES_PASSWORD`
- Mat khau trong `DATABASE_URL`

Gia tri `POSTGRES_PASSWORD` va mat khau trong `DATABASE_URL` phai giong nhau.
Host trong `DATABASE_URL` giu la `inventory-postgres` khi chay bang compose nay.

## 3.1. Tao admin lan dau

Mac dinh he thong khong tao san `admin/admin123`.
Sau khi stack chay, mo:

```text
http://127.0.0.1:8088/setup
```

Hoac mo qua subdomain/domain cua ban. Trang setup chi xuat hien khi database chua co user nao.
Sau khi tao user dau tien, route `/setup` se tu khoa va user do duoc gan quyen Admin.

Neu can cai tu dong khong can mo browser, dat trong env:

```env
INVENTORY_CREATE_ADMIN_FROM_ENV=true
ADMIN_USERNAME=admin
ADMIN_PASSWORD=your-strong-password
ADMIN_EMAIL=admin@company.com
ADMIN_FULL_NAME=System Administrator
```

## 4. Chay Inventory

```bash
cd /opt/docker/compose/inventory
docker compose --env-file /opt/docker/secrets/inventory/inventory.env up -d --build
```

Kiem tra:

```bash
docker compose --env-file /opt/docker/secrets/inventory/inventory.env ps
curl http://127.0.0.1:8088/health
```

Lan dau container len, app se tu:

- Doi `inventory-postgres` san sang
- Tao bang database
- Seed role/permission co ban
- Cho tao admin dau tien tai `/setup`, hoac tao tu env neu bat `INVENTORY_CREATE_ADMIN_FROM_ENV=true`

## 5. Ten container va network

Compose da co ten rieng de khong lan voi dich vu khac:

```text
inventory-app
inventory-nginx
inventory-postgres
inventory-redis
inventory-internal
```

Postgres va Redis khong publish port ra host. Chi cac container trong network `inventory-internal` truy cap duoc.

## 6. Chay qua Cloudflare Tunnel tren miniPC

Giu env:

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

Vi bind la `127.0.0.1`, app khong mo truc tiep ra LAN/WAN, chi tunnel tren may do truy cap duoc.

## 7. Chay LAN noi bo

Neu muon may khac trong mang noi bo truy cap truc tiep:

```env
INVENTORY_HTTP_BIND=0.0.0.0
INVENTORY_HTTP_PORT=8088
PREFERRED_URL_SCHEME=http
FORCE_HTTPS=false
SESSION_COOKIE_SECURE=false
```

Mo:

```text
http://IP_SERVER:8088
```

## 8. Chay server rieng voi domain rieng

Neu server co reverse proxy chung nhu Nginx Proxy Manager, Caddy, Traefik:

- Giu `INVENTORY_HTTP_BIND=127.0.0.1`
- Tro domain ve `http://127.0.0.1:8088`
- De TLS/Let's Encrypt o reverse proxy chung
- Giu `PREFERRED_URL_SCHEME=https`
- Giu `SESSION_COOKIE_SECURE=true`
- Chi bat `FORCE_HTTPS=true` khi proxy gui dung header `X-Forwarded-Proto`

Neu muon dung nginx container cua Inventory truc tiep, co the doi:

```env
INVENTORY_HTTP_BIND=0.0.0.0
INVENTORY_HTTP_PORT=80
INVENTORY_HTTPS_BIND=0.0.0.0
INVENTORY_HTTPS_PORT=443
```

Chi nen lam cach nay khi server chua co reverse proxy khac dang dung port 80/443.

## 9. Backup

Can backup cac thu muc/file sau:

```text
/srv/inventory/data
/srv/postgres/inventory/data
/srv/redis/inventory/data
/opt/docker/secrets/inventory/inventory.env
/opt/docker/configs/inventory
/opt/docker/compose/inventory/compose.yaml
```

Tao backup logic cua app:

```bash
cd /opt/docker/compose/inventory
docker compose --env-file /opt/docker/secrets/inventory/inventory.env exec inventory-app python backup_restore.py backup /app/backups/manual_$(date +%Y%m%d_%H%M%S).zip
```

Restore tu file backup:

```bash
cd /opt/docker/compose/inventory
docker compose --env-file /opt/docker/secrets/inventory/inventory.env exec inventory-app python backup_restore.py restore /app/backups/ten_file_backup.zip
```

Lua chon DB mac dinh la PostgreSQL vi phu hop nhat voi ung dung quan ly thiet bi:

- Du lieu quan he nhieu bang, nhieu khoa ngoai va phan quyen
- Backup/restore bang `pg_dump` va `psql` on dinh
- De tach volume theo service trong `/srv/postgres/inventory/data`
- De mo rong sau nay khi co nhieu nguoi dung, dashboard, audit/log va bao cao

Khong khuyen nghi SQLite cho production/homelab dai han vi backup khi dang ghi de loi hon va kho mo rong hon.

Version PostgreSQL duoc quan ly bang bien:

```env
POSTGRES_IMAGE=postgres:15-alpine
```

Khong nen doi major version truc tiep tren cung data volume. Khi muon nang major version, hay tao backup bang `backup_restore.py`, dung stack, doi image, tao volume moi neu can, roi restore.

## 10. Cap nhat app

```bash
cd /srv/inventory/app
git pull
cp docker-compose.yml /opt/docker/compose/inventory/compose.yaml
cp nginx.conf /opt/docker/configs/inventory/nginx.conf
cp init-ssl.sh /opt/docker/scripts/inventory/init-ssl.sh

cd /opt/docker/compose/inventory
docker compose --env-file /opt/docker/secrets/inventory/inventory.env up -d --build
```

## 11. Them dich vu moi sau nay

Khi them dich vu moi, dung cung pattern:

```text
/opt/docker/compose/<service>/compose.yaml
/opt/docker/configs/<service>/
/opt/docker/scripts/<service>/
/opt/docker/secrets/<service>/<service>.env
/opt/docker/backup/<service>/
/srv/<service>/
/srv/postgres/<service>/data
/srv/redis/<service>/data
```

Khong dung chung DB volume giua cac dich vu. Neu service nao can database rieng, tao folder rieng theo ten service.
