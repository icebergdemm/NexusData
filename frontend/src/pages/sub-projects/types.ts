export interface SubProjectCategory {
  id: number
  name: string
  slug: string
  description: string | null
  color: string
  icon: string
  sort_order: number
  parent_id: number | null
  is_default: boolean
}

export interface SubProjectVersion {
  id: number
  project_id: number
  version_number: number
  filename: string
  file_size: number | null
  changelog: string | null
  deployed_server_1: boolean
  deployed_server_2: boolean
  uploaded_at: string | null
  is_deleted: boolean
}

export interface SubProject {
  id: number
  name: string
  author: string
  project_type: string | null
  crew: string | null
  weapons: string | null
  has_reactor: string | null
  fabricator_medfab: string | null
  has_deconstructor: string | null
  docking_ports: string | null
  decks: string | null
  max_speed: string | null
  extra_equipment: string | null
  features: string | null
  description: string | null
  image_filename: string | null
  discord_message_id: string | null
  created_by_admin_id: number | null
  department_id: number | null
}

export interface SubProjectsListResponse {
  projects: { project: SubProject; version_count: number; latest_version: SubProjectVersion | null; categories: SubProjectCategory[] }[]
  all_categories: SubProjectCategory[]
  all_authors: string[]
  sftp_server_1_name: string
  sftp_server_2_name: string
  can_create: boolean
  can_manage_categories: boolean
  can_manage_test_server: boolean
  can_reorder: boolean
}

export interface SubProjectsTestServerStatus {
  state: string | null
  cpu_percent: number | null
  memory_bytes: number | null
  memory_limit_bytes: number | null
  disk_bytes: number | null
}

export interface SubProjectDetailResponse {
  project: SubProject
  versions: SubProjectVersion[]
  deleted_versions: SubProjectVersion[]
  categories: SubProjectCategory[]
  all_categories: SubProjectCategory[]
  sftp_server_1_name: string
  sftp_server_2_name: string
  is_owner: boolean
  can_edit_this: boolean
  can_delete_this: boolean
  can_upload_version: boolean
  can_delete_version: boolean
  can_restore_version: boolean
  can_download: boolean
  can_deploy_server_1: boolean
  can_deploy_server_2: boolean
  can_undeploy_server_1: boolean
  can_undeploy_server_2: boolean
  can_publish_discord: boolean
  can_change_owner: boolean
  can_edit_categories: boolean
}

// ── "Тестовый полигон ASPER" — department-scoped zone ───────────────────────

export interface PolygonProjectListResponse {
  projects: {
    project: SubProject
    department_name: string | null
    version_count: number
    latest_version: SubProjectVersion | null
    can_edit_this: boolean
  }[]
  creatable_departments: { id: number; name: string }[]
  sftp_server_2_name: string
  has_any_access: boolean
}

export interface PolygonProjectDetailResponse {
  project: SubProject
  department_id: number
  department_name: string
  versions: SubProjectVersion[]
  can_edit_this: boolean
  sftp_server_2_name: string
}
