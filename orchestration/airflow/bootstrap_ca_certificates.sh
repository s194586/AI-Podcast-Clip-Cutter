#!/bin/sh
set -eu

readonly CUSTOM_CA_DIR="/opt/airflow/custom-ca"
readonly CUSTOM_CA_SOURCE="${CUSTOM_CA_DIR}/root-ca.pem"
readonly CUSTOM_CA_TARGET="/usr/local/share/ca-certificates/podcast-cutter-local-root.crt"
readonly SYSTEM_CA_BUNDLE="/etc/ssl/certs/ca-certificates.crt"

is_true() {
    case "${1:-}" in
        1|true|TRUE|yes|YES|on|ON) return 0 ;;
        *) return 1 ;;
    esac
}

fail() {
    printf '%s\n' "CA bootstrap error: $1" >&2
    exit 1
}

[ "$(id -u)" = "0" ] || fail "startup must run as root before dropping to the Airflow user."
[ -f "$SYSTEM_CA_BUNDLE" ] || fail "system CA bundle is missing at $SYSTEM_CA_BUNDLE."

if [ -e "$CUSTOM_CA_SOURCE" ]; then
    [ -f "$CUSTOM_CA_SOURCE" ] || fail "custom CA must be a regular file named root-ca.pem."
    [ ! -L "$CUSTOM_CA_SOURCE" ] || fail "custom CA symlinks are not allowed."
    [ "$(readlink -f "$CUSTOM_CA_SOURCE")" = "$CUSTOM_CA_SOURCE" ] \
        || fail "custom CA resolved outside the fixed secret location."
    [ "$(wc -c < "$CUSTOM_CA_SOURCE")" -le 1048576 ] \
        || fail "custom CA exceeds the 1 MiB size limit."
    openssl x509 -in "$CUSTOM_CA_SOURCE" -noout -checkend 0 >/dev/null 2>&1 \
        || fail "custom CA is not a valid, unexpired PEM certificate."
    openssl x509 -in "$CUSTOM_CA_SOURCE" -noout -text 2>/dev/null \
        | grep -q "CA:TRUE" \
        || fail "custom certificate is not marked as a certificate authority."

    install -m 0644 -o root -g root "$CUSTOM_CA_SOURCE" "$CUSTOM_CA_TARGET"
    update-ca-certificates >/dev/null 2>&1
    printf '%s\n' "CA bootstrap: installed validated custom root certificate."
elif is_true "${CUSTOM_CA_REQUIRED:-false}"; then
    fail "CUSTOM_CA_REQUIRED is enabled but $CUSTOM_CA_SOURCE is missing."
else
    update-ca-certificates >/dev/null 2>&1
    printf '%s\n' "CA bootstrap: using the standard Linux trust store."
fi

[ -s "$SYSTEM_CA_BUNDLE" ] || fail "system CA bundle is empty after update."

export SSL_CERT_FILE="$SYSTEM_CA_BUNDLE"
export REQUESTS_CA_BUNDLE="$SYSTEM_CA_BUNDLE"
export CURL_CA_BUNDLE="$SYSTEM_CA_BUNDLE"
export GRPC_DEFAULT_SSL_ROOTS_FILE_PATH="$SYSTEM_CA_BUNDLE"
export HOME="/home/airflow"

exec setpriv --reuid=airflow --regid=0 --init-groups /entrypoint "$@"
