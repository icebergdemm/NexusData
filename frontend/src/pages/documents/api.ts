import { api } from "@/lib/api"

export type AccessLevel = "view" | "comment" | "edit" | "owner"
export type DocFileType = "word" | "cell" | "pdf" | "image"

export interface DocAdminRef {
  id: number
  name: string | null
  avatar?: string | null
}

export interface DocumentRow {
  id: number
  title: string
  icon: string | null
  file_type: DocFileType
  folder_id: number | null
  parent_document_id: number | null
  department_id: number | null
  department_folder_id: number | null
  owner: DocAdminRef | null
  last_edited_by: DocAdminRef | null
  is_archived: boolean
  is_deleted: boolean
  deleted_at: string | null
  created_at: string | null
  updated_at: string | null
  version: number
  is_starred: boolean
  access_level: AccessLevel | null
  is_mine: boolean
  // Only populated when the current admin owns the document — a
  // human-readable "who else can see this" summary for its card.
  shared_summary: string | null
}

export interface DocumentFull {
  id: number
  title: string
  icon: string | null
  file_type: DocFileType
  folder_id: number | null
  parent_document_id: number | null
  department_id: number | null
  department_folder_id: number | null
  owner: DocAdminRef | null
  last_edited_by: DocAdminRef | null
  version: number
  is_archived: boolean
  created_at: string | null
  updated_at: string | null
  access_level: AccessLevel
  is_starred: boolean
  discord_message_id: string | null
}

export interface DocChildRef {
  id: number
  title: string
  icon: string | null
}

export interface DocFolder {
  id: number
  name: string
  parent_id: number | null
}

export interface DocPermission {
  id: number
  grantee_type: "admin" | "privilege_group" | "access_level" | "everyone"
  grantee_admin: DocAdminRef | null
  grantee_group: { id: number; name: string } | null
  grantee_access_level: { key: string; name: string; is_base: boolean } | null
  level: "view" | "comment" | "edit"
}

export interface DocPermissionSuggestion {
  grantee_type: "admin" | "access_level"
  grantee_id: number | string
  level: "view" | "comment" | "edit"
  label: string
  is_base: boolean
}

export interface DocTag {
  id: number
  name: string
  color: string
}

export interface DocRevision {
  id: number
  title: string | null
  version: number
  saved_by: DocAdminRef | null
  created_at: string | null
}

export interface DocTemplate {
  id: number
  name: string
  description: string | null
  icon: string | null
  file_type: DocFileType
  is_order_template: boolean
  created_by: DocAdminRef | null
  created_at: string | null
}

export interface OnlyOfficeConfig {
  documentServerUrl: string
  config: Record<string, unknown>
}

export type DocScope = "mine" | "shared" | "starred" | "trash" | "all"

const base = "/api/v1/admin/documents"

export const documentsApi = {
  list: (params: { scope: DocScope; folder_id?: number | null; search?: string }) => {
    const qs = new URLSearchParams({ scope: params.scope })
    if (params.folder_id != null) qs.set("folder_id", String(params.folder_id))
    if (params.search) qs.set("search", params.search)
    return api.get<{ documents: DocumentRow[] }>(`${base}?${qs}`)
  },
  get: (id: number) => api.get<DocumentFull>(`${base}/${id}`),
  create: (data: { title?: string; folder_id?: number | null; template_id?: number; department_id?: number; department_folder_id?: number; parent_document_id?: number; is_order_template?: boolean; is_news?: boolean; file_type?: DocFileType }) =>
    api.post<{ id: number }>(base, data),
  import: (file: File, opts: { department_id?: number; folder_id?: number; department_folder_id?: number; parent_document_id?: number; is_order_template?: boolean; is_news?: boolean } = {}) => {
    const form = new FormData()
    form.append("file", file)
    if (opts.department_id != null) form.append("department_id", String(opts.department_id))
    if (opts.folder_id != null) form.append("folder_id", String(opts.folder_id))
    if (opts.department_folder_id != null) form.append("department_folder_id", String(opts.department_folder_id))
    if (opts.parent_document_id != null) form.append("parent_document_id", String(opts.parent_document_id))
    if (opts.is_order_template) form.append("is_order_template", "true")
    if (opts.is_news) form.append("is_news", "true")
    return api.postForm<{ id: number }>(`${base}/import`, form)
  },
  update: (id: number, data: { title?: string; icon?: string | null; folder_id?: number | null; department_folder_id?: number | null }) =>
    api.patch<{ status: string; version: number; updated_at: string }>(`${base}/${id}`, data),
  duplicate: (id: number) => api.post<{ id: number }>(`${base}/${id}/duplicate`),
  trash: (id: number) => api.delete(`${base}/${id}`),
  restore: (id: number) => api.post(`${base}/${id}/restore`),
  permanentDelete: (id: number) => api.delete(`${base}/${id}/permanent`),
  star: (id: number) => api.post(`${base}/${id}/star`),
  unstar: (id: number) => api.delete(`${base}/${id}/star`),
  children: (id: number) => api.get<{ children: DocChildRef[] }>(`${base}/${id}/children`),
  byDepartment: (departmentId: number, opts: { kind?: "documentation" | "orders" | "news"; folderId?: number } = {}) => {
    const qs = new URLSearchParams()
    if (opts.kind) qs.set("kind", opts.kind)
    if (opts.folderId != null) qs.set("folder_id", String(opts.folderId))
    const suffix = qs.toString() ? `?${qs}` : ""
    return api.get<{ documents: DocumentRow[] }>(`/api/v1/admin/departments/${departmentId}/documents${suffix}`)
  },
  publishToDiscord: (id: number, channelId?: string) =>
    api.post<{ status: string; channel_id: string; is_sync: boolean }>(`${base}/${id}/publish-discord`, channelId ? { channel_id: channelId } : {}),
  verifyDiscordChannel: (id: number, channelId: string) =>
    api.post<{ verified: boolean; skipped: boolean; channel_name: string | null }>(`${base}/${id}/discord/verify-channel`, { channel_id: channelId }),
  onlyofficeConfig: (id: number) => api.get<OnlyOfficeConfig>(`${base}/${id}/onlyoffice-config`),

  folders: {
    list: () => api.get<{ folders: DocFolder[] }>(`${base}/folders`),
    create: (data: { name: string; parent_id?: number | null }) => api.post<DocFolder>(`${base}/folders`, data),
    update: (id: number, data: { name?: string; parent_id?: number | null }) => api.patch(`${base}/folders/${id}`, data),
    delete: (id: number) => api.delete(`${base}/folders/${id}`),
  },

  shareCandidates: (documentId: number) => api.get<{
    admins: { id: number; name: string | null }[]
    groups: { id: number | string; name: string; is_base?: boolean }[]
    // "access_level" when the document belongs to a department (groups are
    // THAT department's access levels), else "privilege_group" (ND-wide).
    group_grantee_type: "access_level" | "privilege_group"
    is_department_doc?: boolean
  }>(`${base}/share-candidates?document_id=${documentId}`),
  permissions: {
    list: (documentId: number) => api.get<{ owner: DocAdminRef | null; permissions: DocPermission[]; is_department_doc: boolean }>(`${base}/${documentId}/permissions`),
    grant: (documentId: number, data: { grantee_type: "admin" | "privilege_group" | "access_level" | "everyone"; grantee_id?: number | string | null; level: "view" | "comment" | "edit" }) =>
      api.post(`${base}/${documentId}/permissions`, data),
    revoke: (documentId: number, permissionId: number) => api.delete(`${base}/${documentId}/permissions/${permissionId}`),
    suggestedDefaults: (documentId: number) => api.get<{ suggestions: DocPermissionSuggestion[] }>(`${base}/${documentId}/permissions/suggested-defaults`),
  },

  revisions: {
    list: (documentId: number) => api.get<{ revisions: DocRevision[] }>(`${base}/${documentId}/revisions`),
    restore: (documentId: number, revisionId: number) => api.post<{ status: string; version: number }>(`${base}/${documentId}/revisions/${revisionId}/restore`),
  },

  tags: {
    list: (documentId: number) => api.get<{ tags: DocTag[]; available: DocTag[] }>(`${base}/${documentId}/tags`),
    add: (documentId: number, tagId: number) => api.post<{ status: string }>(`${base}/${documentId}/tags`, { tag_id: tagId }),
    remove: (documentId: number, tagId: number) => api.delete<{ status: string }>(`${base}/${documentId}/tags/${tagId}`),
  },

  templates: {
    list: () => api.get<{ templates: DocTemplate[] }>(`${base}/templates`),
    update: (id: number, data: { name?: string; description?: string; icon?: string }) =>
      api.patch(`${base}/templates/${id}`, data),
    delete: (id: number) => api.delete(`${base}/templates/${id}`),
  },

  saveAsTemplate: (documentId: number, data: { name: string; description?: string; icon?: string }) =>
    api.post<DocTemplate>(`${base}/${documentId}/save-as-template`, data),
}

export const ACCESS_LABEL: Record<AccessLevel, string> = {
  view: "Просмотр",
  comment: "Комментирование",
  edit: "Редактирование",
  owner: "Владелец",
}

export function canEditDocument(level: AccessLevel | null | undefined): boolean {
  return level === "edit" || level === "owner"
}

export function canCommentDocument(level: AccessLevel | null | undefined): boolean {
  return level === "comment" || level === "edit" || level === "owner"
}
