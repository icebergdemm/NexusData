import type { JSONContent } from "@tiptap/react"
import { api } from "@/lib/api"

export type AccessLevel = "view" | "comment" | "edit" | "owner"

export interface DocAdminRef {
  id: number
  name: string | null
  avatar?: string | null
}

export interface DocumentRow {
  id: number
  title: string
  icon: string | null
  folder_id: number | null
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
}

export interface DocumentFull {
  id: number
  title: string
  icon: string | null
  content: JSONContent | null
  folder_id: number | null
  owner: DocAdminRef | null
  last_edited_by: DocAdminRef | null
  version: number
  is_archived: boolean
  created_at: string | null
  updated_at: string | null
  access_level: AccessLevel
  is_starred: boolean
}

export interface DocFolder {
  id: number
  name: string
  parent_id: number | null
}

export interface DocPermission {
  id: number
  grantee_type: "admin" | "privilege_group" | "everyone"
  grantee_admin: DocAdminRef | null
  grantee_group: { id: number; name: string } | null
  level: "view" | "comment" | "edit"
}

export interface DocRevision {
  id: number
  title: string | null
  version: number
  saved_by: DocAdminRef | null
  created_at: string | null
}

export interface DocComment {
  id: number
  parent_id: number | null
  author: DocAdminRef | null
  body: string
  anchor_text: string | null
  mark_id: string | null
  resolved: boolean
  created_at: string | null
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
  create: (data: { title?: string; folder_id?: number | null }) => api.post<{ id: number }>(base, data),
  update: (id: number, data: { title?: string; icon?: string | null; folder_id?: number | null; content?: JSONContent; base_version?: number }) =>
    api.patch<{ status: string; version: number; updated_at: string }>(`${base}/${id}`, data),
  duplicate: (id: number) => api.post<{ id: number }>(`${base}/${id}/duplicate`),
  trash: (id: number) => api.delete(`${base}/${id}`),
  restore: (id: number) => api.post(`${base}/${id}/restore`),
  permanentDelete: (id: number) => api.delete(`${base}/${id}/permanent`),
  star: (id: number) => api.post(`${base}/${id}/star`),
  unstar: (id: number) => api.delete(`${base}/${id}/star`),

  folders: {
    list: () => api.get<{ folders: DocFolder[] }>(`${base}/folders`),
    create: (data: { name: string; parent_id?: number | null }) => api.post<DocFolder>(`${base}/folders`, data),
    update: (id: number, data: { name?: string; parent_id?: number | null }) => api.patch(`${base}/folders/${id}`, data),
    delete: (id: number) => api.delete(`${base}/folders/${id}`),
  },

  shareCandidates: () => api.get<{ admins: { id: number; name: string | null }[]; groups: { id: number; name: string }[] }>(`${base}/share-candidates`),
  permissions: {
    list: (documentId: number) => api.get<{ owner: DocAdminRef | null; permissions: DocPermission[] }>(`${base}/${documentId}/permissions`),
    grant: (documentId: number, data: { grantee_type: "admin" | "privilege_group" | "everyone"; grantee_id?: number | null; level: "view" | "comment" | "edit" }) =>
      api.post(`${base}/${documentId}/permissions`, data),
    revoke: (documentId: number, permissionId: number) => api.delete(`${base}/${documentId}/permissions/${permissionId}`),
  },

  revisions: {
    list: (documentId: number) => api.get<{ revisions: DocRevision[] }>(`${base}/${documentId}/revisions`),
    get: (documentId: number, revisionId: number) => api.get<{ id: number; title: string | null; content: JSONContent; created_at: string }>(`${base}/${documentId}/revisions/${revisionId}`),
    restore: (documentId: number, revisionId: number) => api.post<{ status: string; version: number }>(`${base}/${documentId}/revisions/${revisionId}/restore`),
  },

  comments: {
    list: (documentId: number) => api.get<{ comments: DocComment[] }>(`${base}/${documentId}/comments`),
    create: (documentId: number, data: { body: string; anchor_text?: string; mark_id?: string; parent_id?: number }) =>
      api.post<DocComment>(`${base}/${documentId}/comments`, data),
    resolve: (documentId: number, commentId: number, resolved: boolean) =>
      api.patch(`${base}/${documentId}/comments/${commentId}`, { resolved }),
    delete: (documentId: number, commentId: number) => api.delete(`${base}/${documentId}/comments/${commentId}`),
  },

  uploadImage: (documentId: number, file: File) => {
    const form = new FormData()
    form.append("file", file)
    return api.postForm<{ url: string }>(`${base}/${documentId}/upload-image`, form)
  },
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
