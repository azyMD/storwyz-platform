#!/usr/bin/env bash
set -euo pipefail

backup_dir="${STORWYZ_BACKUP_DIR:-/var/backups/storwyz/postgres}"
database="${STORWYZ_DATABASE:-superchat_agent}"
retention_days="${STORWYZ_BACKUP_RETENTION_DAYS:-14}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
base_name="${database}_${timestamp}"
temporary_dump="${backup_dir}/.${base_name}.dump.tmp"
final_dump="${backup_dir}/${base_name}.dump"
checksum_file="${final_dump}.sha256"
globals_file="${backup_dir}/postgres_globals_${timestamp}.sql"

mkdir -p "${backup_dir}"
chmod 700 "${backup_dir}"

cleanup() {
    rm -f "${temporary_dump}"
}
trap cleanup EXIT

pg_dump --format=custom --compress=6 --file="${temporary_dump}" "${database}"
pg_restore --list "${temporary_dump}" >/dev/null
mv "${temporary_dump}" "${final_dump}"
(
    cd "${backup_dir}"
    sha256sum "$(basename "${final_dump}")"
) >"${checksum_file}"
pg_dumpall --globals-only >"${globals_file}"
chmod 600 "${final_dump}" "${checksum_file}" "${globals_file}"

find "${backup_dir}" -maxdepth 1 -type f \
    \( -name "${database}_*.dump" -o -name "${database}_*.dump.sha256" -o -name "postgres_globals_*.sql" \) \
    -mtime "+${retention_days}" -delete

echo "backup=${final_dump} verified=true retention_days=${retention_days}"
