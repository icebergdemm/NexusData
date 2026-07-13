export interface SubProjectCategory {
  id: number
  name: string
  slug: string
  description: string | null
  color: string
  icon: string
  sort_order: number
  parent_id: number | null
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
}

export interface SubProjectsListResponse {
  projects: { project: SubProject; version_count: number; latest_version: SubProjectVersion | null; categories: SubProjectCategory[] }[]
  all_categories: SubProjectCategory[]
  all_authors: string[]
  sftp_server_1_name: string
  sftp_server_2_name: string
  can_edit: boolean
  can_delete: boolean
}

export interface SubProjectDetailResponse {
  project: SubProject
  versions: SubProjectVersion[]
  categories: SubProjectCategory[]
  sftp_server_1_name: string
  sftp_server_2_name: string
  is_owner: boolean
  can_edit_this: boolean
  can_deploy: boolean
  can_delete: boolean
}
