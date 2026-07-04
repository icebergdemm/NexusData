"""
Sub-projects service: CRUD, SFTP deployment, Discord webhook embeds, filelist.xml management.
"""

import os
import io
import logging
import shutil
import xml.etree.ElementTree as ET
from typing import Optional, List, Tuple, Dict
from datetime import datetime

import httpx
import paramiko
from sqlalchemy.orm import Session
from fastapi import HTTPException, UploadFile

from models import SubProject, SubProjectVersion
from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


# ══════════════════════════════════════════════════════════════════════════════
# SFTP Helper
# ══════════════════════════════════════════════════════════════════════════════

class SFTPManager:
    """Manages SFTP connections to game servers."""

    def __init__(self, server_number: int):
        if server_number == 1:
            self.host = settings.sftp_server_1_host
            self.port = settings.sftp_server_1_port
            self.username = settings.sftp_server_1_username
            self.password = settings.sftp_server_1_password
            self.key_path = settings.sftp_server_1_key_path
            self.remote_dir = settings.sftp_server_1_remote_dir
            self.name = settings.sftp_server_1_name
        elif server_number == 2:
            self.host = settings.sftp_server_2_host
            self.port = settings.sftp_server_2_port
            self.username = settings.sftp_server_2_username
            self.password = settings.sftp_server_2_password
            self.key_path = settings.sftp_server_2_key_path
            self.remote_dir = settings.sftp_server_2_remote_dir
            self.name = settings.sftp_server_2_name
        else:
            raise ValueError(f"Invalid server number: {server_number}")

        self.server_number = server_number

    def _connect(self) -> paramiko.SFTPClient:
        """Establish SFTP connection with timeouts to prevent app freezes."""
        if not self.host:
            raise HTTPException(status_code=500, detail=f"SFTP сервер {self.server_number} не настроен")

        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(15)  # 15s connection timeout
        try:
            sock.connect((self.host, self.port))
        except (socket.timeout, OSError) as e:
            sock.close()
            raise HTTPException(status_code=504, detail=f"SFTP сервер {self.name} недоступен: {e}")

        transport = paramiko.Transport(sock)
        transport.set_keepalive(30)  # Keepalive every 30s
        # Banner/auth timeout
        transport.banner_timeout = 15
        transport.auth_timeout = 15

        try:
            if self.key_path and os.path.exists(self.key_path):
                pkey = paramiko.RSAKey.from_private_key_file(self.key_path)
                transport.connect(username=self.username, pkey=pkey)
            elif self.password:
                transport.connect(username=self.username, password=self.password)
            else:
                transport.close()
                raise HTTPException(status_code=500, detail=f"Нет учётных данных для SFTP сервера {self.server_number}")
        except Exception as e:
            transport.close()
            raise HTTPException(status_code=500, detail=f"Ошибка аутентификации SFTP {self.name}: {e}")

        sftp = paramiko.SFTPClient.from_transport(transport)
        sftp.get_channel().settimeout(30)  # 30s per-operation timeout
        sftp._nd_transport = transport  # Keep reference for cleanup
        return sftp

    def _close_sftp(self, sftp):
        """Close SFTP client AND its underlying transport."""
        if not sftp:
            return
        transport = getattr(sftp, '_nd_transport', None)
        try:
            sftp.close()
        except Exception:
            pass
        if transport:
            try:
                transport.close()
            except Exception:
                pass

    def upload_file(self, local_path: str, remote_filename: str) -> bool:
        """Upload a .sub file to the remote server."""
        sftp = None
        try:
            sftp = self._connect()
            remote_path = f"{self.remote_dir.rstrip('/')}/{remote_filename}"
            sftp.put(local_path, remote_path)
            logger.info(f"Uploaded {remote_filename} to server {self.server_number}: {remote_path}")
            return True
        except Exception as e:
            logger.error(f"SFTP upload failed (server {self.server_number}): {e}")
            raise HTTPException(status_code=500, detail=f"Ошибка загрузки на {self.name}: {e}")
        finally:
            self._close_sftp(sftp)

    def delete_file(self, remote_filename: str) -> bool:
        """Delete a .sub file from the remote server."""
        sftp = None
        try:
            sftp = self._connect()
            remote_path = f"{self.remote_dir.rstrip('/')}/{remote_filename}"
            sftp.remove(remote_path)
            logger.info(f"Deleted {remote_filename} from server {self.server_number}")
            return True
        except FileNotFoundError:
            logger.warning(f"File not found on server {self.server_number}: {remote_filename}")
            return True  # Already gone
        except Exception as e:
            logger.error(f"SFTP delete failed (server {self.server_number}): {e}")
            raise HTTPException(status_code=500, detail=f"Ошибка удаления с {self.name}: {e}")
        finally:
            self._close_sftp(sftp)

    def read_filelist_xml(self) -> Optional[str]:
        """Read filelist.xml from the remote server."""
        sftp = None
        try:
            sftp = self._connect()
            remote_path = f"{self.remote_dir.rstrip('/')}/filelist.xml"
            with sftp.open(remote_path, 'r') as f:
                content = f.read()
            if isinstance(content, bytes):
                content = content.decode('utf-8')
            return content
        except FileNotFoundError:
            logger.warning(f"filelist.xml not found on server {self.server_number}")
            return None
        except Exception as e:
            logger.error(f"SFTP read filelist.xml failed (server {self.server_number}): {e}")
            raise HTTPException(status_code=500, detail=f"Ошибка чтения filelist.xml с {self.name}: {e}")
        finally:
            self._close_sftp(sftp)

    def write_filelist_xml(self, content: str) -> bool:
        """Write filelist.xml to the remote server."""
        sftp = None
        try:
            sftp = self._connect()
            remote_path = f"{self.remote_dir.rstrip('/')}/filelist.xml"
            with sftp.open(remote_path, 'w') as f:
                f.write(content)
            logger.info(f"Updated filelist.xml on server {self.server_number}")
            return True
        except Exception as e:
            logger.error(f"SFTP write filelist.xml failed (server {self.server_number}): {e}")
            raise HTTPException(status_code=500, detail=f"Ошибка записи filelist.xml на {self.name}: {e}")
        finally:
            self._close_sftp(sftp)

    def add_to_filelist(self, sub_filename: str) -> bool:
        """Add a .sub file entry to filelist.xml on the remote server."""
        xml_content = self.read_filelist_xml()
        if xml_content is None:
            # Create minimal filelist.xml matching Barotrauma format
            xml_content = (
                '<?xml version="1.0" encoding="utf-8"?>\n'
                '<contentpackage name="Projects_Main" modversion="1.0.0" '
                'corepackage="False" gameversion="1.11.5.0">\n'
                '</contentpackage>'
            )

        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError:
            logger.error(f"Failed to parse filelist.xml on server {self.server_number}")
            raise HTTPException(status_code=500, detail=f"Ошибка парсинга filelist.xml на {self.name}")

        # Check if already exists
        for elem in root:
            file_attr = elem.get("file", "")
            if sub_filename in file_attr:
                logger.info(f"{sub_filename} already in filelist.xml on server {self.server_number}")
                return True

        # Add new Submarine element with %ModDir% path
        sub_elem = ET.SubElement(root, "Submarine")
        sub_elem.set("file", f"%ModDir%/{sub_filename}")

        # Write back with proper formatting
        new_xml = self._serialize_filelist(root)
        return self.write_filelist_xml(new_xml)

    def _serialize_filelist(self, root: ET.Element) -> str:
        """Serialize filelist.xml preserving self-closing tags and formatting."""
        # Build XML manually to get proper self-closing <Submarine ... /> tags
        attrs = ' '.join(f'{k}="{v}"' for k, v in root.attrib.items())
        lines = [f'<?xml version="1.0" encoding="utf-8"?>']
        lines.append(f'<contentpackage {attrs}>' if attrs else '<contentpackage>')
        for child in root:
            child_attrs = ' '.join(f'{k}="{v}"' for k, v in child.attrib.items())
            lines.append(f'  <{child.tag} {child_attrs} />')
        lines.append('</contentpackage>')
        return '\n'.join(lines)

    def remove_from_filelist(self, sub_filename: str) -> bool:
        """Remove a .sub file entry from filelist.xml on the remote server."""
        xml_content = self.read_filelist_xml()
        if xml_content is None:
            return True

        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError:
            logger.error(f"Failed to parse filelist.xml on server {self.server_number}")
            raise HTTPException(status_code=500, detail=f"Ошибка парсинга filelist.xml на {self.name}")

        # Find and remove matching elements
        to_remove = []
        for elem in root:
            file_attr = elem.get("file", "")
            if sub_filename in file_attr:
                to_remove.append(elem)

        for elem in to_remove:
            root.remove(elem)

        if to_remove:
            new_xml = self._serialize_filelist(root)
            return self.write_filelist_xml(new_xml)

        return True


# ══════════════════════════════════════════════════════════════════════════════
# Sub-Project Service
# ══════════════════════════════════════════════════════════════════════════════

class SubProjectService:
    """CRUD and business logic for sub-projects."""

    def __init__(self, db: Session):
        self.db = db
        self._ensure_upload_dir()

    def _ensure_upload_dir(self):
        """Ensure upload directories exist."""
        os.makedirs(settings.sub_projects_upload_dir, exist_ok=True)
        os.makedirs(os.path.join(settings.sub_projects_upload_dir, "images"), exist_ok=True)
        os.makedirs(os.path.join(settings.sub_projects_upload_dir, "files"), exist_ok=True)

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def get_all(
        self,
        search: Optional[str] = None,
        author: Optional[str] = None,
        crew_min: Optional[int] = None,
        crew_max: Optional[int] = None,
        category_id: Optional[int] = None,
    ) -> List[SubProject]:
        """Get all sub-projects with optional filters (excludes soft-deleted)."""
        from models import SubProjectCategoryLink
        from sqlalchemy import or_

        q = self.db.query(SubProject).filter(SubProject.is_deleted == 0)

        # Search by name
        if search:
            q = q.filter(SubProject.name.ilike(f"%{search}%"))

        # Filter by author
        if author:
            q = q.filter(SubProject.author.ilike(f"%{author}%"))

        # Filter by category
        if category_id:
            q = q.join(SubProjectCategoryLink).filter(SubProjectCategoryLink.category_id == category_id)

        projects = q.order_by(SubProject.name).all()

        # Filter by crew range (crew is stored as "min-avg-max" string)
        if crew_min is not None or crew_max is not None:
            filtered = []
            for p in projects:
                if not p.crew:
                    continue
                parts = p.crew.split("-")
                try:
                    p_min = int(parts[0]) if len(parts) >= 1 else 0
                    p_max = int(parts[-1]) if len(parts) >= 1 else 0
                except (ValueError, IndexError):
                    continue
                if crew_min is not None and p_max < crew_min:
                    continue
                if crew_max is not None and p_min > crew_max:
                    continue
                filtered.append(p)
            projects = filtered

        return projects

    def get_by_id(self, project_id: int) -> Optional[SubProject]:
        return self.db.query(SubProject).filter(SubProject.id == project_id).first()

    def create(self, data: dict, admin_id: int) -> SubProject:
        project = SubProject(
            name=data["name"],
            project_type=data.get("project_type"),
            crew=data.get("crew"),
            weapons=data.get("weapons"),
            has_reactor=data.get("has_reactor"),
            fabricator_medfab=data.get("fabricator_medfab"),
            has_deconstructor=data.get("has_deconstructor"),
            docking_ports=data.get("docking_ports"),
            decks=data.get("decks"),
            max_speed=data.get("max_speed"),
            extra_equipment=data.get("extra_equipment"),
            features=data.get("features"),
            description=data.get("description"),
            author=data["author"],
            created_by_admin_id=admin_id,
        )
        self.db.add(project)
        self.db.commit()
        self.db.refresh(project)
        return project

    def update(self, project_id: int, data: dict) -> Optional[SubProject]:
        project = self.get_by_id(project_id)
        if not project:
            return None

        updatable_fields = [
            "name", "project_type", "crew", "weapons", "has_reactor",
            "fabricator_medfab", "has_deconstructor", "docking_ports",
            "decks", "max_speed", "extra_equipment", "features",
            "description", "author",
        ]
        for field in updatable_fields:
            if field in data:
                setattr(project, field, data[field])

        self.db.commit()
        self.db.refresh(project)
        return project

    def delete(self, project_id: int) -> bool:
        """Soft-delete a project (mark as deleted, preserve data)."""
        project = self.get_by_id(project_id)
        if not project:
            return False

        project.is_deleted = 1
        project.deleted_at = datetime.utcnow()
        self.db.commit()
        return True

    # ── Image ─────────────────────────────────────────────────────────────────

    def save_image(self, project_id: int, file: UploadFile) -> str:
        """Save project image, return filename."""
        project = self.get_by_id(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Проект не найден")

        # Delete old image
        if project.image_filename:
            old_path = os.path.join(settings.sub_projects_upload_dir, "images", project.image_filename)
            if os.path.exists(old_path):
                os.remove(old_path)

        # Save new image
        ext = os.path.splitext(file.filename)[1] or ".png"
        safe_name = f"project_{project_id}_{int(datetime.utcnow().timestamp())}{ext}"
        save_path = os.path.join(settings.sub_projects_upload_dir, "images", safe_name)

        with open(save_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        project.image_filename = safe_name
        self.db.commit()
        return safe_name

    # ── Versions ──────────────────────────────────────────────────────────────

    def get_versions(self, project_id: int, include_deleted: bool = False) -> List[SubProjectVersion]:
        q = (
            self.db.query(SubProjectVersion)
            .filter(SubProjectVersion.project_id == project_id)
        )
        if not include_deleted:
            q = q.filter(SubProjectVersion.is_deleted == 0)
        return q.order_by(SubProjectVersion.version_number.desc()).all()

    def get_version_by_id(self, version_id: int) -> Optional[SubProjectVersion]:
        return self.db.query(SubProjectVersion).filter(SubProjectVersion.id == version_id).first()

    def upload_version(self, project_id: int, file: UploadFile, changelog: str, admin_id: int) -> SubProjectVersion:
        """Upload a new .sub file version."""
        project = self.get_by_id(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Проект не найден")

        # Determine next version number
        latest = (
            self.db.query(SubProjectVersion)
            .filter(SubProjectVersion.project_id == project_id)
            .order_by(SubProjectVersion.version_number.desc())
            .first()
        )
        next_version = (latest.version_number + 1) if latest else 1

        # Save file
        ext = os.path.splitext(file.filename)[1] or ".sub"
        stored_name = f"project_{project_id}_v{next_version}{ext}"
        save_path = os.path.join(settings.sub_projects_upload_dir, "files", stored_name)

        file.file.seek(0, 2)
        file_size = file.file.tell()
        file.file.seek(0)

        with open(save_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        version = SubProjectVersion(
            project_id=project_id,
            version_number=next_version,
            filename=file.filename,
            stored_filename=stored_name,
            file_size=file_size,
            changelog=changelog or None,
            uploaded_by_admin_id=admin_id,
        )
        self.db.add(version)
        self.db.commit()
        self.db.refresh(version)
        return version

    def delete_version(self, version_id: int, admin_id: Optional[int] = None) -> bool:
        """Soft-delete a version (mark as deleted, preserve file)."""
        version = self.get_version_by_id(version_id)
        if not version:
            return False

        version.is_deleted = 1
        version.deleted_at = datetime.utcnow()
        version.deleted_by_admin_id = admin_id
        self.db.commit()
        return True

    def restore_version(self, version_id: int) -> bool:
        """Restore a soft-deleted version."""
        version = self.get_version_by_id(version_id)
        if not version:
            return False
        version.is_deleted = 0
        version.deleted_at = None
        version.deleted_by_admin_id = None
        self.db.commit()
        return True

    # ── Categories ────────────────────────────────────────────────────────────

    def get_all_categories(self) -> list:
        """Get all categories ordered by sort_order."""
        from models import SubProjectCategory
        return self.db.query(SubProjectCategory).order_by(SubProjectCategory.sort_order, SubProjectCategory.name).all()

    def get_category_by_id(self, cat_id: int):
        from models import SubProjectCategory
        return self.db.query(SubProjectCategory).filter(SubProjectCategory.id == cat_id).first()

    def create_category(self, name: str, slug: str, description: Optional[str] = None,
                        color: str = "#1a68de", icon: str = "🚢",
                        sort_order: int = 0, parent_id: Optional[int] = None,
                        admin_id: Optional[int] = None):
        from models import SubProjectCategory
        import re
        if not slug:
            slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
        cat = SubProjectCategory(
            name=name, slug=slug, description=description,
            color=color, icon=icon, sort_order=sort_order,
            parent_id=parent_id, created_by_admin_id=admin_id,
        )
        self.db.add(cat)
        self.db.commit()
        self.db.refresh(cat)
        return cat

    def update_category(self, cat_id: int, data: dict):
        from models import SubProjectCategory
        cat = self.get_category_by_id(cat_id)
        if not cat:
            return None
        for field in ("name", "slug", "description", "color", "icon", "sort_order", "parent_id"):
            if field in data:
                setattr(cat, field, data[field])
        self.db.commit()
        self.db.refresh(cat)
        return cat

    def delete_category(self, cat_id: int) -> bool:
        from models import SubProjectCategory, SubProjectCategoryLink
        cat = self.get_category_by_id(cat_id)
        if not cat:
            return False
        # Remove links first
        self.db.query(SubProjectCategoryLink).filter(SubProjectCategoryLink.category_id == cat_id).delete()
        # Unparent children
        self.db.query(SubProjectCategory).filter(SubProjectCategory.parent_id == cat_id).update({"parent_id": None})
        self.db.delete(cat)
        self.db.commit()
        return True

    def assign_category(self, project_id: int, category_id: int):
        from models import SubProjectCategoryLink
        existing = self.db.query(SubProjectCategoryLink).filter(
            SubProjectCategoryLink.project_id == project_id,
            SubProjectCategoryLink.category_id == category_id,
        ).first()
        if existing:
            return existing
        link = SubProjectCategoryLink(project_id=project_id, category_id=category_id)
        self.db.add(link)
        self.db.commit()
        return link

    def remove_category(self, project_id: int, category_id: int) -> bool:
        from models import SubProjectCategoryLink
        link = self.db.query(SubProjectCategoryLink).filter(
            SubProjectCategoryLink.project_id == project_id,
            SubProjectCategoryLink.category_id == category_id,
        ).first()
        if not link:
            return False
        self.db.delete(link)
        self.db.commit()
        return True

    def get_project_categories(self, project_id: int) -> list:
        from models import SubProjectCategory, SubProjectCategoryLink
        return (
            self.db.query(SubProjectCategory)
            .join(SubProjectCategoryLink)
            .filter(SubProjectCategoryLink.project_id == project_id)
            .order_by(SubProjectCategory.sort_order, SubProjectCategory.name)
            .all()
        )

    def get_unique_authors(self) -> list:
        """Get unique authors for filter dropdown."""
        rows = self.db.query(SubProject.author).distinct().order_by(SubProject.author).all()
        return [r[0] for r in rows if r[0]]

    # ── SFTP Deployment ───────────────────────────────────────────────────────

    def deploy_version(self, version_id: int, server_number: int) -> bool:
        """Deploy a .sub file version to a remote server via SFTP."""
        version = self.get_version_by_id(version_id)
        if not version:
            raise HTTPException(status_code=404, detail="Версия не найдена")

        local_path = os.path.join(settings.sub_projects_upload_dir, "files", version.stored_filename)
        if not os.path.exists(local_path):
            raise HTTPException(status_code=404, detail="Файл версии не найден на диске")

        # Use original filename for remote (game server expects it)
        remote_filename = version.filename

        sftp = SFTPManager(server_number)
        sftp.upload_file(local_path, remote_filename)
        sftp.add_to_filelist(remote_filename)

        # Mark as deployed
        if server_number == 1:
            version.deployed_server_1 = 1
        else:
            version.deployed_server_2 = 1
        self.db.commit()
        return True

    def undeploy_version(self, version_id: int, server_number: int) -> bool:
        """Remove a .sub file from a remote server."""
        version = self.get_version_by_id(version_id)
        if not version:
            raise HTTPException(status_code=404, detail="Версия не найдена")

        remote_filename = version.filename

        sftp = SFTPManager(server_number)
        sftp.delete_file(remote_filename)
        sftp.remove_from_filelist(remote_filename)

        # Mark as undeployed
        if server_number == 1:
            version.deployed_server_1 = 0
        else:
            version.deployed_server_2 = 0
        self.db.commit()
        return True

    # ── Discord Bot Publishing ────────────────────────────────────────────────

    def build_embed(self, project: SubProject, image_url: Optional[str] = None) -> dict:
        """Build a Discord embed dict for the project."""
        fields = []

        def _add_field(name: str, value: str, inline: bool = False):
            if value:
                fields.append({"name": name, "value": value, "inline": inline})

        _add_field("Тип проекта", project.project_type or "—")
        _add_field("Экипаж", project.crew or "—")
        _add_field("Вооружение", project.weapons or "—")
        _add_field("Реактор", project.has_reactor or "—")
        _add_field("Фабрикатор / Мед. фабрикатор", project.fabricator_medfab or "—")
        _add_field("Деконструктор", project.has_deconstructor or "—")
        _add_field("Стыковочные порты", project.docking_ports or "—")
        _add_field("Палубы", project.decks or "—")
        _add_field("Максимальная скорость", project.max_speed or "—")
        _add_field("Доп. оборудование", project.extra_equipment or "—")
        _add_field("Особенности", project.features or "—")
        _add_field("Краткое описание", project.description or "—")
        _add_field("Автор", project.author)

        embed = {
            "title": project.name,
            "color": 0x1a68de,
            "fields": fields,
            "footer": {"text": "Проектировщик КБ «Гайка»"},
            "timestamp": datetime.utcnow().isoformat(),
        }

        if image_url:
            embed["image"] = {"url": image_url}

        return embed

    def publish_to_discord(self, project_id: int, base_url: str) -> Optional[str]:
        """Publish or update Discord embed for a project via the Discord bot.
        
        Uses DiscordBotAction queue to send/edit messages through the bot
        in the configured sub_projects channel. Falls back to webhook if
        channel is not configured.
        """
        project = self.get_by_id(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Проект не найден")

        # Build image URL
        image_url = None
        if project.image_filename:
            image_url = f"{base_url}/sub_projects/images/{project.image_filename}"

        embed = self.build_embed(project, image_url)

        # Try bot-based publishing first (via channel)
        channel_id = settings.sub_projects_discord_channel_id
        if channel_id:
            return self._publish_via_bot(project, embed, channel_id)

        # Fallback to webhook
        webhook_url = settings.sub_projects_webhook_url
        if not webhook_url:
            raise HTTPException(status_code=400, detail="Ни канал Discord бота, ни Webhook URL не настроены")

        return self._publish_via_webhook(project, embed, webhook_url)

    def _publish_via_bot(self, project: SubProject, embed: dict, channel_id: str) -> Optional[str]:
        """Publish embed via Discord bot action queue."""
        from models import DiscordBotAction

        if project.discord_message_id:
            # Edit existing message via bot
            action = DiscordBotAction(
                action_type="edit_embed",
                payload={
                    "channel_id": channel_id,
                    "message_id": project.discord_message_id,
                    "embed": embed,
                },
            )
            self.db.add(action)
            self.db.commit()
            logger.info(f"Queued edit_embed bot action for project {project.id}")
            return project.discord_message_id
        else:
            # Send new message via bot
            action = DiscordBotAction(
                action_type="send_sub_project_embed",
                payload={
                    "channel_id": channel_id,
                    "embed": embed,
                    "project_id": project.id,
                },
            )
            self.db.add(action)
            self.db.commit()
            logger.info(f"Queued send_sub_project_embed bot action for project {project.id}")
            # Message ID will be set by bot after sending
            return "pending"

    def _publish_via_webhook(self, project: SubProject, embed: dict, webhook_url: str) -> Optional[str]:
        """Fallback: publish embed via Discord webhook."""
        if project.discord_message_id:
            # Edit existing message
            edit_url = f"{webhook_url}/messages/{project.discord_message_id}"
            try:
                resp = httpx.patch(edit_url, json={"embeds": [embed]}, timeout=10.0)
                if resp.status_code == 200:
                    return project.discord_message_id
                else:
                    logger.warning(f"Discord edit failed ({resp.status_code}), sending new message")
                    project.discord_message_id = None
            except Exception as e:
                logger.warning(f"Discord edit error: {e}, sending new message")
                project.discord_message_id = None

        # Send new message
        try:
            resp = httpx.post(
                f"{webhook_url}?wait=true",
                json={"embeds": [embed]},
                timeout=10.0,
            )
            if resp.status_code in (200, 201, 204):
                data = resp.json()
                message_id = data.get("id")
                project.discord_message_id = message_id
                self.db.commit()
                return message_id
            else:
                logger.error(f"Discord webhook failed: {resp.status_code} {resp.text}")
                raise HTTPException(status_code=500, detail=f"Discord webhook вернул {resp.status_code}")
        except httpx.HTTPError as e:
            logger.error(f"Discord webhook error: {e}")
            raise HTTPException(status_code=500, detail=f"Ошибка отправки в Discord: {e}")

    def delete_discord_message(self, project_id: int) -> bool:
        """Delete the Discord embed message for a project."""
        project = self.get_by_id(project_id)
        if not project or not project.discord_message_id:
            return False

        channel_id = settings.sub_projects_discord_channel_id
        if channel_id:
            # Delete via bot
            from models import DiscordBotAction
            action = DiscordBotAction(
                action_type="delete_message",
                payload={
                    "channel_id": channel_id,
                    "message_id": project.discord_message_id,
                },
            )
            self.db.add(action)
            project.discord_message_id = None
            self.db.commit()
            return True

        # Fallback to webhook
        webhook_url = settings.sub_projects_webhook_url
        if not webhook_url:
            return False

        delete_url = f"{webhook_url}/messages/{project.discord_message_id}"
        try:
            resp = httpx.delete(delete_url, timeout=10.0)
            if resp.status_code in (200, 204, 404):
                project.discord_message_id = None
                self.db.commit()
                return True
            else:
                logger.error(f"Discord delete failed: {resp.status_code}")
                return False
        except Exception as e:
            logger.error(f"Discord delete error: {e}")
            return False
