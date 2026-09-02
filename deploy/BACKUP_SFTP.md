# Encrypted off-site SFTP backups

NexusData creates a MySQL logical dump, a consistent SQLite log-index snapshot,
and the configured file trees. It streams them into a gzip tar archive encrypted
with AES-256-GCM **before** SFTP upload. Only the encrypted `.tar.gz.aes` object
is uploaded; the temporary local working directory is deleted after success or
failure. Application logs record only a backup ID, byte count and result, never
credentials or backed-up log contents.

The live NexusData log index is still its operational SQLite database. This
feature encrypts the off-site copy; encrypting the live database at rest would
be a separate SQLCipher migration.

## 0. Rotate the exposed root credential

If a real root password was pasted into chat, a ticket or a log, rotate it now.
Do not put `SFTP_USER=root` or `SFTP_PASSWORD=...` into NexusData. The backup
module deliberately rejects root and does not implement password auth.

`BACKUP_SFTP_HOST` is `213.108.170.48`, not `https://213.108.170.48`.

## 1. Create a dedicated client key on the NexusData host

Run as the same OS user that runs NexusData:

```bash
install -d -m 0700 /home/container/.ssh
ssh-keygen -t ed25519 -f /home/container/.ssh/nexus_backup
chmod 0600 /home/container/.ssh/nexus_backup
```

Use a passphrase if your process can supply it through
`BACKUP_SFTP_PRIVATE_KEY_PASSPHRASE`. Back up the private key securely; never
copy it to the SFTP server.

## 2. Create the restricted SFTP account

Transfer only `nexus_backup.pub` through the provider console or another trusted
channel. On the backup host, run this repository's script as root:

```bash
sudo ./deploy/provision_backup_sftp.sh /root/nexus_backup.pub
```

This creates `nexusbackup`, locks its password, chroots it to
`/srv/nexus-backups`, forces `internal-sftp`, and disables shell, TTY, tunneling
and forwarding. `/data` is its only writable directory.

If the host is actively scanned and NexusData has a stable public IP, also
allow TCP/22 only from that IP in the provider firewall (or, for example, UFW),
then deny other sources. Keep console access before changing firewall rules.

## 3. Pin and verify the SSH host key

On the backup host/provider console, obtain the authoritative fingerprint:

```bash
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256
```

On the NexusData host, collect the public host key and compare its fingerprint
to the value above over the independent console channel:

```bash
ssh-keyscan -p 22 -t ed25519 213.108.170.48 > /home/container/.ssh/nexus_backup_known_hosts
ssh-keygen -lf /home/container/.ssh/nexus_backup_known_hosts -E sha256
chmod 0644 /home/container/.ssh/nexus_backup_known_hosts
```

Do not trust an unverified `ssh-keyscan` result by itself. NexusData uses
Paramiko `RejectPolicy`; an unknown or changed key stops the backup.

## 4. Configure NexusData

Generate the independent archive key:

```bash
python -c "import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

Store a copy of that key somewhere **other than** the NexusData and backup
servers. Losing it makes every backup unrecoverable.

Add to `.env`:

```dotenv
BACKUP_ENABLED=false
BACKUP_INTERVAL_HOURS=24
BACKUP_RETENTION_COUNT=14
BACKUP_SFTP_HOST=213.108.170.48
BACKUP_SFTP_PORT=22
BACKUP_SFTP_USERNAME=nexusbackup
BACKUP_SFTP_PRIVATE_KEY_PATH=/home/container/.ssh/nexus_backup
BACKUP_SFTP_PRIVATE_KEY_PASSPHRASE=
BACKUP_SFTP_KNOWN_HOSTS_PATH=/home/container/.ssh/nexus_backup_known_hosts
BACKUP_SFTP_REMOTE_DIR=/data
BACKUP_ENCRYPTION_KEY=PASTE_THE_GENERATED_32_BYTE_BASE64_KEY
BACKUP_INCLUDE_DATABASE=true
# Optional: a dedicated least-privilege dump account; empty uses DATABASE_URL.
BACKUP_DATABASE_URL=
BACKUP_INCLUDE_LOG_INDEX=true
BACKUP_INCLUDE_PATHS=config,uploads,logs
BACKUP_MYSQLDUMP_BINARY=mysqldump
BACKUP_COMMAND_TIMEOUT_SECONDS=1800
```

Install the MySQL/MariaDB client package so `mysqldump` is available. Add the
extra log paths to `BACKUP_INCLUDE_PATHS` when they are provided. `.env`, SSH
keys, common key/certificate files, Git metadata, virtual environments,
`node_modules`, device files and symlinks are excluded from archives.

## 5. Check, run once, then enable the schedule

```bash
python -m services_backup check
python -m services_backup run
```

After the first uploaded archive is visible under `/data`, set
`BACKUP_ENABLED=true` and restart the scheduler/application. A backup runs at
startup and then at `BACKUP_INTERVAL_HOURS`; only the newest
`BACKUP_RETENTION_COUNT` matching NexusData archives are retained.

## 6. Test recovery

Download one encrypted archive through SFTP and restore it into a new directory:

```bash
python -m services_backup restore ./nexusdata-YYYYMMDDTHHMMSSZ-ID.tar.gz.aes ./restore-check
```

The GCM authentication tag detects a wrong key or any modified/corrupt byte.
Inspect `restore-check/manifest.json`, verify the files, and test a database
restore on an isolated MySQL instance. Do not wait for an incident to perform
the first recovery test.
