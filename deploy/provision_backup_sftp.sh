#!/usr/bin/env bash
set -euo pipefail

# Run ONCE from the backup server's root/provider console, not from NexusData:
#   sudo ./provision_backup_sftp.sh /root/nexus_backup.pub
#
# It creates a key-only, chrooted, SFTP-only account.  The account cannot run
# commands, allocate a terminal, or forward ports.

PUBLIC_KEY_FILE="${1:-}"
BACKUP_USER="${2:-nexusbackup}"
CHROOT_DIR="${3:-/srv/nexus-backups}"
DATA_DIR="${CHROOT_DIR}/data"
AUTHORIZED_KEYS_DIR="/etc/ssh/authorized_keys"
AUTHORIZED_KEY_FILE="${AUTHORIZED_KEYS_DIR}/${BACKUP_USER}"
SSHD_DROPIN_DIR="/etc/ssh/sshd_config.d"
SSHD_DROPIN="${SSHD_DROPIN_DIR}/90-nexus-backup.conf"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root on the BACKUP server." >&2
  exit 1
fi
if [[ -z "${PUBLIC_KEY_FILE}" || ! -f "${PUBLIC_KEY_FILE}" ]]; then
  echo "Usage: $0 /path/to/nexus_backup.pub [username] [chroot_dir]" >&2
  exit 1
fi
if [[ ! "${BACKUP_USER}" =~ ^[a-z_][a-z0-9_-]{0,31}$ || "${BACKUP_USER}" == "root" ]]; then
  echo "Unsafe backup username." >&2
  exit 1
fi
if [[ "${CHROOT_DIR}" != /* || "${CHROOT_DIR}" == "/" ]]; then
  echo "The chroot must be an absolute, non-root path." >&2
  exit 1
fi
if ! ssh-keygen -l -f "${PUBLIC_KEY_FILE}" >/dev/null 2>&1; then
  echo "The supplied file is not a valid SSH public key." >&2
  exit 1
fi
if [[ "$(grep -cve '^[[:space:]]*$' "${PUBLIC_KEY_FILE}")" -ne 1 ]]; then
  echo "The public-key file must contain exactly one key." >&2
  exit 1
fi

if getent passwd "${BACKUP_USER}" >/dev/null; then
  usermod --home /data --shell /bin/sh "${BACKUP_USER}"
else
  useradd --no-create-home --home-dir /data --shell /bin/sh --user-group "${BACKUP_USER}"
fi
passwd --lock "${BACKUP_USER}" >/dev/null
BACKUP_GROUP="$(id -gn "${BACKUP_USER}")"

# OpenSSH requires ChrootDirectory and every path component above it to be
# root-owned and not writable by the chrooted user.
install -d -o root -g root -m 0755 "${CHROOT_DIR}"
install -d -o "${BACKUP_USER}" -g "${BACKUP_GROUP}" -m 0700 "${DATA_DIR}"
install -d -o root -g root -m 0755 "${AUTHORIZED_KEYS_DIR}"
install -o root -g root -m 0600 "${PUBLIC_KEY_FILE}" "${AUTHORIZED_KEY_FILE}"

install -d -o root -g root -m 0755 "${SSHD_DROPIN_DIR}"
TEMP_CONFIG="$(mktemp "${SSHD_DROPIN}.XXXXXX")"
PREVIOUS_CONFIG=""
cleanup() {
  rm -f "${TEMP_CONFIG}"
  if [[ -n "${PREVIOUS_CONFIG}" ]]; then
    rm -f "${PREVIOUS_CONFIG}"
  fi
}
trap cleanup EXIT
if [[ -f "${SSHD_DROPIN}" ]]; then
  PREVIOUS_CONFIG="$(mktemp "${SSHD_DROPIN}.previous.XXXXXX")"
  cp --preserve=mode,ownership "${SSHD_DROPIN}" "${PREVIOUS_CONFIG}"
fi
{
  printf '%s\n' "Match User ${BACKUP_USER}"
  printf '%s\n' "    ChrootDirectory ${CHROOT_DIR}"
  printf '%s\n' "    ForceCommand internal-sftp -d /data -u 077"
  printf '%s\n' "    AuthenticationMethods publickey"
  printf '%s\n' "    PubkeyAuthentication yes"
  printf '%s\n' "    PasswordAuthentication no"
  printf '%s\n' "    KbdInteractiveAuthentication no"
  printf '%s\n' "    PermitEmptyPasswords no"
  printf '%s\n' "    AuthorizedKeysFile ${AUTHORIZED_KEYS_DIR}/%u"
  printf '%s\n' "    AllowAgentForwarding no"
  printf '%s\n' "    AllowTcpForwarding no"
  printf '%s\n' "    X11Forwarding no"
  printf '%s\n' "    PermitTTY no"
  printf '%s\n' "    PermitTunnel no"
  printf '%s\n' "Match all"
} >"${TEMP_CONFIG}"
chmod 0600 "${TEMP_CONFIG}"
install -o root -g root -m 0600 "${TEMP_CONFIG}" "${SSHD_DROPIN}"

SSHD_BINARY="$(command -v sshd || true)"
if [[ -z "${SSHD_BINARY}" && -x /usr/sbin/sshd ]]; then
  SSHD_BINARY=/usr/sbin/sshd
fi
if [[ -z "${SSHD_BINARY}" ]] || ! "${SSHD_BINARY}" -t; then
  if [[ -n "${PREVIOUS_CONFIG}" ]]; then
    install -o root -g root -m 0600 "${PREVIOUS_CONFIG}" "${SSHD_DROPIN}"
  else
    rm -f "${SSHD_DROPIN}"
  fi
  echo "sshd configuration validation failed; the previous state was restored." >&2
  exit 1
fi

if command -v systemctl >/dev/null 2>&1; then
  systemctl reload ssh 2>/dev/null || systemctl reload sshd
else
  service ssh reload 2>/dev/null || service sshd reload
fi

echo "Created restricted key-only SFTP account: ${BACKUP_USER}"
echo "Chroot: ${CHROOT_DIR}; writable directory visible to SFTP: /data"
echo "No password was configured and no shell/forwarding is available over SSH."
