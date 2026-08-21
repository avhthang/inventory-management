#!/bin/bash
# ============================================================================
# check-replication.sh
# Kiểm tra trạng thái PostgreSQL Logical Replication
#
# Sử dụng:
#   bash check-replication.sh           # Kiểm tra cơ bản
#   bash check-replication.sh --detail  # Kiểm tra chi tiết
#   bash check-replication.sh --watch   # Theo dõi liên tục (mỗi 5 giây)
# ============================================================================

set -euo pipefail

CONTAINER="inventory-postgres"
DB_NAME="${POSTGRES_DB:-inventory_db}"
DB_USER="${POSTGRES_USER:-inventory_user}"

run_psql() {
    docker exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tAc "$1" 2>/dev/null
}

run_psql_verbose() {
    docker exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -c "$1" 2>/dev/null
}

check_basic() {
    local now
    now=$(date '+%Y-%m-%d %H:%M:%S')
    echo ""
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║  REPLICATION STATUS — $now  ║"
    echo "╚══════════════════════════════════════════════════════════╝"

    # Check container
    if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
        echo -e "\033[1;31m[✗] Container '$CONTAINER' không chạy!\033[0m"
        return 1
    fi

    # Check wal_level
    local wal_level
    wal_level=$(run_psql "SHOW wal_level;")
    echo ""
    echo "── PostgreSQL Config ──"
    echo "  wal_level: $wal_level"

    # Publications
    local pub_count
    pub_count=$(run_psql "SELECT COUNT(*) FROM pg_publication;")
    echo ""
    echo "── Publications: ${pub_count} ──"
    if [ "$pub_count" -gt 0 ]; then
        run_psql_verbose "
            SELECT pubname AS \"Tên\",
                   CASE WHEN puballtables THEN 'Tất cả bảng' ELSE 'Chọn bảng' END AS \"Phạm vi\"
            FROM pg_publication;
        "
    fi

    # Subscriptions
    local sub_count
    sub_count=$(run_psql "SELECT COUNT(*) FROM pg_subscription;")
    echo "── Subscriptions: ${sub_count} ──"
    if [ "$sub_count" -gt 0 ]; then
        run_psql_verbose "
            SELECT subname AS \"Tên\",
                   CASE WHEN subenabled THEN '🟢 ACTIVE' ELSE '🔴 DISABLED' END AS \"Trạng thái\"
            FROM pg_subscription;
        "

        # Subscription workers (replication lag indicator)
        echo "── Replication Workers ──"
        run_psql_verbose "
            SELECT subname AS \"Subscription\",
                   pid AS \"PID\",
                   received_lsn AS \"Received LSN\",
                   latest_end_lsn AS \"Latest LSN\",
                   latest_end_time AS \"Lần đồng bộ cuối\"
            FROM pg_stat_subscription
            WHERE subname IS NOT NULL;
        "
    fi

    # Replication slots
    local slot_count
    slot_count=$(run_psql "SELECT COUNT(*) FROM pg_replication_slots;")
    echo "── Replication Slots: ${slot_count} ──"
    if [ "$slot_count" -gt 0 ]; then
        run_psql_verbose "
            SELECT slot_name AS \"Slot\",
                   slot_type AS \"Loại\",
                   CASE WHEN active THEN '🟢 Active' ELSE '🔴 Inactive' END AS \"Trạng thái\",
                   restart_lsn AS \"Restart LSN\",
                   confirmed_flush_lsn AS \"Confirmed Flush LSN\"
            FROM pg_replication_slots;
        "
    fi

    # WAL senders (on publisher)
    local sender_count
    sender_count=$(run_psql "SELECT COUNT(*) FROM pg_stat_replication;")
    if [ "$sender_count" -gt 0 ]; then
        echo "── WAL Senders (Publisher → Subscriber): ${sender_count} ──"
        run_psql_verbose "
            SELECT pid AS \"PID\",
                   client_addr AS \"Subscriber IP\",
                   state AS \"Trạng thái\",
                   sent_lsn AS \"Sent LSN\",
                   write_lsn AS \"Write LSN\",
                   flush_lsn AS \"Flush LSN\",
                   replay_lsn AS \"Replay LSN\"
            FROM pg_stat_replication;
        "
    fi
}

check_detail() {
    check_basic

    echo ""
    echo "── Chi tiết bảng trong Publication ──"
    run_psql_verbose "
        SELECT pubname AS \"Publication\",
               schemaname AS \"Schema\",
               tablename AS \"Bảng\"
        FROM pg_publication_tables
        ORDER BY pubname, tablename;
    " 2>/dev/null || echo "  (không có publication)"

    echo ""
    echo "── Subscription Errors (nếu có) ──"
    run_psql_verbose "
        SELECT subname, subenabled,
               subconninfo
        FROM pg_subscription;
    " 2>/dev/null || echo "  (không có subscription)"

    # Quick row count comparison hint
    echo ""
    echo "── Số lượng bản ghi các bảng chính ──"
    run_psql_verbose "
        SELECT 'device' AS \"Bảng\", COUNT(*) AS \"Số bản ghi\" FROM device
        UNION ALL
        SELECT 'user', COUNT(*) FROM \"user\"
        UNION ALL
        SELECT 'handover', COUNT(*) FROM handover
        UNION ALL
        SELECT 'department', COUNT(*) FROM department
        UNION ALL
        SELECT 'bug_report', COUNT(*) FROM bug_report
        UNION ALL
        SELECT 'maintenance_log', COUNT(*) FROM maintenance_log
        ORDER BY \"Bảng\";
    " 2>/dev/null || echo "  (không thể đọc số bản ghi)"
}

watch_mode() {
    echo "Theo dõi replication (Ctrl+C để dừng)..."
    while true; do
        clear
        check_basic
        sleep 5
    done
}

# ── Main ──────────────────────────────────────────────────────────────────
case "${1:---basic}" in
    --detail|-d)
        check_detail
        ;;
    --watch|-w)
        watch_mode
        ;;
    *)
        check_basic
        ;;
esac
