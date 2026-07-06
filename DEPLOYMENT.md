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
