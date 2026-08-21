#!/bin/bash
# ============================================================================
# setup-replication.sh
# One-way PostgreSQL Logical Replication Setup
#
# Primary (Publisher): 192.168.110.23 — nhập dữ liệu ở đây
# Replica (Subscriber): 192.168.143.250 — tự động nhận dữ liệu từ Primary
#
# Hướng dẫn sử dụng:
#
#   BƯỚC 1 — Trên Server A (192.168.110.23):
#     cd /opt/docker/compose/inventory
#     bash /srv/inventory/app/setup-replication.sh publisher
#
#   BƯỚC 2 — Trên Server B (192.168.143.250):
#     cd /opt/docker/compose/inventory
#     bash /srv/inventory/app/setup-replication.sh subscriber
#
# Yêu cầu:
#   - Cả 2 server đã chạy docker compose stack (inventory-postgres đang chạy)
#   - Server A đã restart postgres với wal_level=logical (xem README)
#   - Server A đã mở port 5432 ra LAN (POSTGRES_BIND=0.0.0.0 trong env)
#   - Dữ liệu 2 server đã giống nhau trước khi chạy script này
# ============================================================================

set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────────────
PRIMARY_HOST="${PRIMARY_HOST:-192.168.110.23}"
PRIMARY_PG_PORT="${PRIMARY_PG_PORT:-5432}"
REPLICA_HOST="${REPLICA_HOST:-192.168.143.250}"

DB_NAME="${POSTGRES_DB:-inventory_db}"
DB_USER="${POSTGRES_USER:-inventory_user}"
DB_PASS="${POSTGRES_PASSWORD:-inventory_pass}"

PUB_NAME="inventory_pub"
SUB_NAME="inventory_sub"

CONTAINER="inventory-postgres"
# ───────────────────────────────────────────────────────────────────────────

log()  { echo -e "\033[1;32m[✓]\033[0m $*"; }
warn() { echo -e "\033[1;33m[!]\033[0m $*"; }
err()  { echo -e "\033[1;31m[✗]\033[0m $*"; exit 1; }

run_psql() {
    docker exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tAc "$1"
}

run_psql_verbose() {
    docker exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -c "$1"
}

check_container() {
    if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
        err "Container '$CONTAINER' không chạy. Hãy chạy docker compose up trước."
    fi
}

check_wal_level() {
    local level
    level=$(run_psql "SHOW wal_level;")
    if [ "$level" != "logical" ]; then
        err "wal_level hiện tại là '$level', cần 'logical'.\n    Hãy restart postgres với command: postgres -c wal_level=logical\n    (docker compose đã được cấu hình sẵn, chỉ cần restart)"
    fi
    log "wal_level = logical ✓"
}

# ── Publisher Setup (chạy trên Server A) ──────────────────────────────────
setup_publisher() {
    echo ""
    echo "╔══════════════════════════════════════════════════╗"
    echo "║  THIẾT LẬP PUBLISHER (Server A - 192.168.110.23)  ║"
    echo "╚══════════════════════════════════════════════════╝"
    echo ""

    check_container
    check_wal_level

    # Check if publication already exists
    local existing
    existing=$(run_psql "SELECT COUNT(*) FROM pg_publication WHERE pubname = '$PUB_NAME';")
    if [ "$existing" -gt 0 ]; then
        warn "Publication '$PUB_NAME' đã tồn tại."
        read -rp "Bạn muốn xóa và tạo lại? (y/N): " confirm
        if [ "$confirm" = "y" ] || [ "$confirm" = "Y" ]; then
            run_psql "DROP PUBLICATION $PUB_NAME;"
            log "Đã xóa publication cũ"
        else
            log "Giữ nguyên publication hiện tại"
            return
        fi
    fi

    # Create publication for ALL tables
    run_psql "CREATE PUBLICATION $PUB_NAME FOR ALL TABLES;"
    log "Đã tạo publication '$PUB_NAME' cho tất cả bảng"

    # Verify
    echo ""
    log "Danh sách bảng trong publication:"
    run_psql_verbose "SELECT schemaname, tablename FROM pg_publication_tables WHERE pubname = '$PUB_NAME' ORDER BY tablename;"

    echo ""
    log "═══ PUBLISHER SETUP HOÀN TẤT ═══"
    echo ""
    echo "Bước tiếp theo: chạy script trên Server B (192.168.143.250):"
    echo "  bash /srv/inventory/app/setup-replication.sh subscriber"
    echo ""
}

# ── Subscriber Setup (chạy trên Server B) ─────────────────────────────────
setup_subscriber() {
    echo ""
    echo "╔══════════════════════════════════════════════════════╗"
    echo "║  THIẾT LẬP SUBSCRIBER (Server B - 192.168.143.250)    ║"
    echo "╚══════════════════════════════════════════════════════╝"
    echo ""

    check_container

    # Test connectivity to primary
    echo "Kiểm tra kết nối tới Primary (${PRIMARY_HOST}:${PRIMARY_PG_PORT})..."
    if docker exec "$CONTAINER" pg_isready -h "$PRIMARY_HOST" -p "$PRIMARY_PG_PORT" -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
        log "Kết nối tới Primary thành công ✓"
    else
        err "Không thể kết nối tới Primary tại ${PRIMARY_HOST}:${PRIMARY_PG_PORT}.\n    Kiểm tra:\n    1. Server A đã mở port PostgreSQL (POSTGRES_BIND=0.0.0.0)?\n    2. Firewall có cho phép port ${PRIMARY_PG_PORT}?\n    3. 2 server có ping được nhau?"
    fi

    # Verify publication exists on primary
    echo "Kiểm tra publication trên Primary..."
    local pub_check
    pub_check=$(docker exec "$CONTAINER" psql \
        "host=${PRIMARY_HOST} port=${PRIMARY_PG_PORT} dbname=${DB_NAME} user=${DB_USER} password=${DB_PASS}" \
        -tAc "SELECT COUNT(*) FROM pg_publication WHERE pubname = '$PUB_NAME';" 2>/dev/null || echo "0")
    if [ "$pub_check" -lt 1 ]; then
        err "Publication '$PUB_NAME' không tồn tại trên Primary.\n    Hãy chạy script trên Server A trước:\n    bash /srv/inventory/app/setup-replication.sh publisher"
    fi
    log "Publication '$PUB_NAME' tồn tại trên Primary ✓"

    # Check if subscription already exists
    local existing
    existing=$(run_psql "SELECT COUNT(*) FROM pg_subscription WHERE subname = '$SUB_NAME';")
    if [ "$existing" -gt 0 ]; then
        warn "Subscription '$SUB_NAME' đã tồn tại."
        read -rp "Bạn muốn xóa và tạo lại? (y/N): " confirm
        if [ "$confirm" = "y" ] || [ "$confirm" = "Y" ]; then
            run_psql "ALTER SUBSCRIPTION $SUB_NAME DISABLE;"
            run_psql "ALTER SUBSCRIPTION $SUB_NAME SET (slot_name = NONE);"
            run_psql "DROP SUBSCRIPTION $SUB_NAME;"
            log "Đã xóa subscription cũ"

            # Clean up replication slot on primary
            warn "Đang dọn replication slot trên Primary..."
            docker exec "$CONTAINER" psql \
                "host=${PRIMARY_HOST} port=${PRIMARY_PG_PORT} dbname=${DB_NAME} user=${DB_USER} password=${DB_PASS}" \
                -c "SELECT pg_drop_replication_slot(slot_name) FROM pg_replication_slots WHERE slot_name LIKE '${SUB_NAME}%';" 2>/dev/null || true
        else
            log "Giữ nguyên subscription hiện tại"
            return
        fi
    fi

    # Create subscription
    # copy_data = false: vì 2 DB đã giống nhau, không cần copy lại
    # origin = none: tránh loop nếu sau này bật bi-directional
    echo ""
    echo "Đang tạo subscription..."
    run_psql_verbose "
        CREATE SUBSCRIPTION $SUB_NAME
        CONNECTION 'host=${PRIMARY_HOST} port=${PRIMARY_PG_PORT} dbname=${DB_NAME} user=${DB_USER} password=${DB_PASS}'
        PUBLICATION $PUB_NAME
        WITH (copy_data = false, origin = none);
    "
    log "Đã tạo subscription '$SUB_NAME'"

    # Wait a moment for replication to start
    sleep 3

    # Verify subscription status
    echo ""
    log "Trạng thái subscription:"
    run_psql_verbose "
        SELECT subname,
               CASE WHEN subenabled THEN 'ACTIVE' ELSE 'DISABLED' END as status,
               subconninfo
        FROM pg_subscription
        WHERE subname = '$SUB_NAME';
    "

    # Check subscription workers
    log "Replication workers:"
    run_psql_verbose "
        SELECT pid, subname, received_lsn, latest_end_lsn, latest_end_time
        FROM pg_stat_subscription
        WHERE subname = '$SUB_NAME';
    "

    echo ""
    log "═══ SUBSCRIBER SETUP HOÀN TẤT ═══"
    echo ""
    echo "Dữ liệu nhập trên Server A (192.168.110.23) sẽ tự động"
    echo "đồng bộ sang Server B (192.168.143.250) trong vài giây."
    echo ""
    echo "Kiểm tra trạng thái bất kỳ lúc nào:"
    echo "  bash /srv/inventory/app/check-replication.sh"
    echo ""
}

# ── Teardown ──────────────────────────────────────────────────────────────
teardown() {
    echo ""
    echo "╔══════════════════════════════════╗"
    echo "║  GỠ BỎ REPLICATION               ║"
    echo "╚══════════════════════════════════╝"
    echo ""

    check_container

    read -rp "Bạn đang chạy lệnh này trên server nào? (A/B): " server

    if [ "$server" = "B" ] || [ "$server" = "b" ]; then
        local sub_exists
        sub_exists=$(run_psql "SELECT COUNT(*) FROM pg_subscription WHERE subname = '$SUB_NAME';")
        if [ "$sub_exists" -gt 0 ]; then
            run_psql "ALTER SUBSCRIPTION $SUB_NAME DISABLE;"
            run_psql "ALTER SUBSCRIPTION $SUB_NAME SET (slot_name = NONE);"
            run_psql "DROP SUBSCRIPTION $SUB_NAME;"
            log "Đã xóa subscription '$SUB_NAME' trên Server B"
        else
            warn "Subscription '$SUB_NAME' không tồn tại"
        fi
    elif [ "$server" = "A" ] || [ "$server" = "a" ]; then
        # Clean up replication slots
        run_psql "SELECT pg_drop_replication_slot(slot_name) FROM pg_replication_slots WHERE slot_name LIKE '${SUB_NAME}%';" 2>/dev/null || true

        local pub_exists
        pub_exists=$(run_psql "SELECT COUNT(*) FROM pg_publication WHERE pubname = '$PUB_NAME';")
        if [ "$pub_exists" -gt 0 ]; then
            run_psql "DROP PUBLICATION $PUB_NAME;"
            log "Đã xóa publication '$PUB_NAME' trên Server A"
        else
            warn "Publication '$PUB_NAME' không tồn tại"
        fi
    else
        err "Vui lòng chọn A hoặc B"
    fi

    echo ""
    log "═══ TEARDOWN HOÀN TẤT ═══"
}

# ── Status check ──────────────────────────────────────────────────────────
check_status() {
    check_container

    echo ""
    echo "── Publications (nếu đây là Publisher) ──"
    run_psql_verbose "SELECT pubname, puballtables FROM pg_publication;" 2>/dev/null || echo "(không có)"

    echo ""
    echo "── Subscriptions (nếu đây là Subscriber) ──"
    run_psql_verbose "
        SELECT subname,
               CASE WHEN subenabled THEN 'ACTIVE' ELSE 'DISABLED' END as status
        FROM pg_subscription;" 2>/dev/null || echo "(không có)"

    echo ""
    echo "── Replication Slots ──"
    run_psql_verbose "
        SELECT slot_name, active, restart_lsn, confirmed_flush_lsn
        FROM pg_replication_slots;" 2>/dev/null || echo "(không có)"

    echo ""
    echo "── Subscription Workers ──"
    run_psql_verbose "
        SELECT pid, subname, received_lsn, latest_end_lsn, latest_end_time
        FROM pg_stat_subscription;" 2>/dev/null || echo "(không có)"
}

# ── Main ──────────────────────────────────────────────────────────────────
case "${1:-help}" in
    publisher|pub)
        setup_publisher
        ;;
    subscriber|sub)
        setup_subscriber
        ;;
    teardown|remove)
        teardown
        ;;
    status|check)
        check_status
        ;;
    *)
        echo "Inventory PostgreSQL Replication Setup"
        echo ""
        echo "Sử dụng:"
        echo "  $0 publisher     Thiết lập Publisher trên Server A (192.168.110.23)"
        echo "  $0 subscriber    Thiết lập Subscriber trên Server B (192.168.143.250)"
        echo "  $0 status        Kiểm tra trạng thái replication"
        echo "  $0 teardown      Gỡ bỏ replication"
        echo ""
        echo "Biến môi trường (tùy chọn):"
        echo "  PRIMARY_HOST      IP Server A (mặc định: 192.168.110.23)"
        echo "  PRIMARY_PG_PORT   Port PostgreSQL Server A (mặc định: 5432)"
        echo "  POSTGRES_DB       Tên database (mặc định: inventory_db)"
        echo "  POSTGRES_USER     User PostgreSQL (mặc định: inventory_user)"
        echo "  POSTGRES_PASSWORD Password PostgreSQL (mặc định: inventory_pass)"
        echo ""
        exit 1
        ;;
esac
