# Deployment

Inventory hien duoc trien khai theo mo hinh tach service:

```text
/opt/docker/compose/inventory/compose.yaml
/opt/docker/configs/inventory/
/opt/docker/scripts/inventory/
/opt/docker/secrets/inventory/inventory.env
/opt/docker/backup/inventory/

/srv/inventory/
/srv/postgres/inventory/data
/srv/redis/inventory/data
```

Hay dung [HOMELAB_DEPLOYMENT.md](HOMELAB_DEPLOYMENT.md) lam tai lieu chinh.

Cai nhanh tu GitHub:

```bash
git clone https://github.com/avhthang/inventory-management.git /tmp/inventory-management
sudo sh /tmp/inventory-management/install.sh
```

Chay khong tuong tac:

```bash
# MiniPC + Cloudflare Tunnel
sudo INSTALL_MODE=tunnel DOMAIN=inventory.example.com sh /tmp/inventory-management/install.sh

# Server/domain qua reverse proxy ngoai
sudo INSTALL_MODE=proxy DOMAIN=inventory.example.com sh /tmp/inventory-management/install.sh

# Server/domain chay truc tiep port 80/443
sudo INSTALL_MODE=direct DOMAIN=inventory.example.com sh /tmp/inventory-management/install.sh
```

Lenh chay nhanh tren server:

```bash
cd /opt/docker/compose/inventory
docker compose --env-file /opt/docker/secrets/inventory/inventory.env up -d --build
```

Kiem tra:

```bash
docker compose --env-file /opt/docker/secrets/inventory/inventory.env ps
curl http://127.0.0.1:8088/health
```

Container chinh:

```text
inventory-app
inventory-nginx
inventory-postgres
inventory-redis
```

Database va Redis khong publish port ra host. Chung chi nam trong network rieng `inventory-internal`.

Admin dau tien duoc tao tai `/setup` khi database chua co user nao. PostgreSQL la database mac dinh cho production.
