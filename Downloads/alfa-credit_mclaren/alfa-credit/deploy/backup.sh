#!/bin/sh
# Ежедневный бэкап (cron): 0 3 * * * cd /opt/alfa && sh deploy/backup.sh
set -e; mkdir -p backups; F="backups/alfa-$(date +%F).sql.gz"
docker compose -f docker-compose.prod.yml exec -T db pg_dump -U alfa alfa | gzip > "$F"; find backups -name '*.sql.gz' -mtime +14 -delete; echo "$F"
# восстановление: gunzip -c backups/alfa-YYYY-MM-DD.sql.gz | docker compose -f docker-compose.prod.yml exec -T db psql -U alfa alfa
