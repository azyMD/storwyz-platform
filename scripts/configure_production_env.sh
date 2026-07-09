#!/usr/bin/env bash
set -euo pipefail

env_file="${1:-/opt/superchat-ai-agent/.env}"

if [[ ! -f "${env_file}" ]]; then
    echo "Environment file not found: ${env_file}" >&2
    exit 1
fi

secret="$(sed -n 's/^DJANGO_SECRET_KEY=//p' "${env_file}" | head -n 1)"
if [[ -z "${secret}" ]]; then
    secret="$(openssl rand -hex 64)"
fi

temporary_file="$(mktemp "${env_file}.XXXXXX")"
cleanup() {
    rm -f "${temporary_file}"
}
trap cleanup EXIT

awk -F= '
    $1 != "DJANGO_SECRET_KEY" &&
    $1 != "DJANGO_DEBUG" &&
    $1 != "DJANGO_SECURE_SSL_REDIRECT" &&
    $1 != "DJANGO_SESSION_COOKIE_SECURE" &&
    $1 != "DJANGO_CSRF_COOKIE_SECURE" &&
    $1 != "DJANGO_SECURE_HSTS_SECONDS" &&
    $1 != "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS" &&
    $1 != "DJANGO_SECURE_HSTS_PRELOAD"
' "${env_file}" >"${temporary_file}"

{
    printf '\nDJANGO_SECRET_KEY=%s\n' "${secret}"
    printf 'DJANGO_DEBUG=False\n'
    printf 'DJANGO_SECURE_SSL_REDIRECT=True\n'
    printf 'DJANGO_SESSION_COOKIE_SECURE=True\n'
    printf 'DJANGO_CSRF_COOKIE_SECURE=True\n'
    printf 'DJANGO_SECURE_HSTS_SECONDS=3600\n'
    printf 'DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=False\n'
    printf 'DJANGO_SECURE_HSTS_PRELOAD=False\n'
} >>"${temporary_file}"

chown --reference="${env_file}" "${temporary_file}"
chmod --reference="${env_file}" "${temporary_file}"
mv "${temporary_file}" "${env_file}"
trap - EXIT

echo "Configured production Django security values in ${env_file}."
