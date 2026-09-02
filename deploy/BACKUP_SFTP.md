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

## 0. Password-only quick setup

The compact mode accepts the existing `SFTP_IP`, `SFTP_PORT`, `SFTP_USER` and
`SFTP_PASSWORD` settings. The password is never written to application logs or
subprocess arguments. `BACKUP_ENCRYPTION_KEY` is still mandatory because it
encrypts the backup itself before upload.

When `SFTP_PASSWORD` is non-empty and `BACKUP_SFTP_PASSWORD` is empty, these
four compact values form one profile and take precedence over leftover
`BACKUP_SFTP_HOST`, username and private-key settings.

```dotenv
SFTP_IP=213.108.170.48
SFTP_PORT=22
SFTP_USER=your-sftp-user
SFTP_PASSWORD=your-sftp-password
BACKUP_ENCRYPTION_KEY=PASTE_THE_GENERATED_32_BYTE_BASE64_KEY
BACKUP_SFTP_REMOTE_DIR=/data
BACKUP_ENABLED=false
```

An `https://` prefix is tolerated and stripped, though a bare IP/hostname is
clearer. If `BACKUP_SFTP_KNOWN_HOSTS_PATH` is empty, NexusData creates
`data/backup_known_hosts`: the first connection enrolls the server key and all
later connections reject a changed key (trust on first use).

Using `root` is supported but strongly discouraged: anyone who obtains the site
`.env` then obtains full access to the backup host. The restricted setup below
contains the impact to backup files only.

## 1. Optional recommended restricted key setup

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

## 3. Optional manual host-key verification

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

Do not trust an unverified `ssh-keyscan` result by itself. With a pre-populated
file NexusData uses strict verification; after automatic first-use enrollment,
an unknown or changed key also stops the backup.

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
BACKUP_SFTP_PASSWORD=
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

If `mysqldump` is installed NexusData uses it. In restricted containers without
system-package access, it automatically falls back to a streaming schema/data
export through the already installed PyMySQL driver. Add the extra log paths to
`BACKUP_INCLUDE_PATHS` when they are provided. `.env`, SSH keys, common
key/certificate files, Git metadata, virtual environments, `node_modules`,
device files and symlinks are excluded from archives.

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
