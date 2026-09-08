import { api } from "@/lib/api"

export type TaskPriority = "low" | "medium" | "high" | "urgent" | "ultra"
export type TaskStatus = "review" | "in_progress" | "done"

export const PRIORITY_LABELS: Record<TaskPriority, string> = {
  low: "Низкий",
  medium: "Средний",
  high: "Высокий",
  urgent: "Срочно",
  ultra: "УЛЬТРА СУПЕР МЕГА ОЧЕНЬ СРОЧНЫЙ СПАТЬ НЕЛЬЗЯ РАБОТАТЬ НАДО",
}

export const PRIORITY_COLORS: Record<TaskPriority, string> = {
  low: "#5A646E",
  medium: "#3B82F6",
  high: "#F59E0B",
  urgent: "#EF4444",
  ultra: "#F43F5E",
}

export const STATUS_LABELS: Record<TaskStatus, string> = {
  review: "Рассмотрение",
  in_progress: "В работе",
  done: "Завершено",
}

export const STATUS_COLORS: Record<TaskStatus, string> = {
  review: "#8B9ECF",
  in_progress: "#3B82F6",
  done: "#22C55E",
}

export interface TaskBoardRow {
  id: number
  name: string
  description: string | null
  icon: string
  color: string
  department_id: number | null
  department_name: string | null
  created_by_name: string | null
  created_at: string | null
  task_count: number | null
}

export interface TaskColumnRow {
  id: number
  board_id: number
  name: string
  color: string
  sort_order: number
}

export interface TaskRow {
  id: number
  board_id: number
  column_id: number
  title: string
  description: string | null
  deadline: string | null
  priority: TaskPriority | null
  tags: string[]
  assignee_admin_id: number | null
  assignee_admin_name: string | null
  assignee_department_id: number | null
  assignee_department_name: string | null
  created_by_admin_id: number | null
  created_by_name: string | null
  sort_order: number
  status: TaskStatus
  is_hidden: boolean
  rejection_reason: string | null
  preview_attachment_id: number | null
  preview_url: string | null
  attachment_count: number
  created_at: string | null
  updated_at: string | null
  comment_count: number
}

export interface TaskComment {
  id: number
  task_id: number
  admin_id: number | null
  admin_name: string | null
  admin_avatar: string | null
  content: string
  created_at: string | null
}

export interface TaskBoardDetail {
  board: TaskBoardRow
  columns: TaskColumnRow[]
  tasks: TaskRow[]
  can_manage_boards: boolean
  can_create: boolean
  can_edit_all: boolean
  can_delete_all: boolean
  my_board_perms?: { view: boolean; create: boolean; edit: boolean; delete: boolean }
}

export interface AssigneeCandidates {
  admins: { id: number; name: string | null }[]
  departments: { id: number; name: string; icon: string | null }[]
}

export interface BoardPermission {
  id?: number
  grantee_type: "group_nd" | "user" | "dept_level"
  grantee_key: string
  can_view: boolean
  can_create: boolean
  can_edit: boolean
  can_delete: boolean
}

export interface TaskAttachment {
  id: number
  task_id: number
  filename: string
  mime_type: string | null
  file_size: number | null
  is_preview: boolean
  url: string
  created_at: string | null
  uploader_name: string | null
}

export interface TaskActivity {
  id: number
  task_id: number
  admin_id: number | null
  admin_name: string | null
  action: string
  detail: string | null
  created_at: string | null
}

export interface BoardMessageAttachment {
  id: number
  filename: string
  url: string
  mime_type: string | null
}

export interface BoardMessage {
  id: number
  board_id: number
  author_admin_id: number | null
  author_name: string | null
  author_avatar: string | null
  content: string
  reply_to_id: number | null
  is_pinned: boolean
  created_at: string | null
  attachments: BoardMessageAttachment[]
}

const base = "/api/v1/admin/tasks"

export const tasksApi = {
  boards: {
    list: () => api.get<{ boards: TaskBoardRow[]; can_manage_boards: boolean; can_create: boolean }>(`${base}/boards`),
    get: (boardId: number, showHidden = false) => api.get<TaskBoardDetail>(`${base}/boards/${boardId}${showHidden ? "?show_hidden=true" : ""}`),
    create: (body: { name: string; description?: string; icon?: string; color?: string; department_id?: number | null }) =>
      api.post<{ status: string; board: TaskBoardRow }>(`${base}/boards`, body),
    update: (boardId: number, body: { name?: string; description?: string; icon?: string; color?: string; department_id?: number | null }) =>
      api.patch<{ status: string; board: TaskBoardRow }>(`${base}/boards/${boardId}`, body),
    archive: (boardId: number) => api.delete<{ status: string }>(`${base}/boards/${boardId}`),
    permissions: {
      get: (boardId: number) => api.get<{ permissions: BoardPermission[] }>(`${base}/boards/${boardId}/permissions`),
      set: (boardId: number, permissions: BoardPermission[]) => api.put<{ status: string }>(`${base}/boards/${boardId}/permissions`, { permissions }),
    },
    chat: {
      list: (boardId: number, params?: { limit?: number; before_id?: number }) => {
        const qs = new URLSearchParams()
        if (params?.limit) qs.set("limit", String(params.limit))
        if (params?.before_id) qs.set("before_id", String(params.before_id))
        return api.get<{ messages: BoardMessage[]; pinned: BoardMessage[] }>(`${base}/boards/${boardId}/chat?${qs}`)
      },
      send: (boardId: number, formData: FormData) => api.postForm<{ status: string; message: BoardMessage }>(`${base}/boards/${boardId}/chat`, formData),
      sendJson: (boardId: number, body: { content: string; reply_to_id?: number | null }) => api.post<{ status: string; message: BoardMessage }>(`${base}/boards/${boardId}/chat`, body),
      togglePin: (boardId: number, messageId: number) => api.post<{ status: string; is_pinned: boolean }>(`${base}/boards/${boardId}/chat/${messageId}/pin`, {}),
    },
  },

  columns: {
    create: (boardId: number, body: { name: string; color?: string }) =>
      api.post<{ status: string; column: TaskColumnRow }>(`${base}/boards/${boardId}/columns`, body),
    update: (boardId: number, columnId: number, body: { name?: string; color?: string }) =>
      api.patch<{ status: string; column: TaskColumnRow }>(`${base}/boards/${boardId}/columns/${columnId}`, body),
    reorder: (boardId: number, columnIds: number[]) =>
      api.post<{ status: string }>(`${base}/boards/${boardId}/columns/reorder`, { column_ids: columnIds }),
    delete: (boardId: number, columnId: number) =>
      api.delete<{ status: string }>(`${base}/boards/${boardId}/columns/${columnId}`),
  },

  tasks: {
    create: (boardId: number, body: {
      title: string; column_id?: number; description?: string; deadline?: string | null
      priority?: TaskPriority | null; tags?: string[]
      assignee_admin_id?: number | null; assignee_department_id?: number | null
    }) => api.post<{ status: string; task: TaskRow }>(`${base}/boards/${boardId}/tasks`, body),
    update: (taskId: number, body: Partial<{
      title: string; description: string | null; deadline: string | null
      priority: TaskPriority | null; tags: string[]
      assignee_admin_id: number | null; assignee_department_id: number | null
      column_id: number; sort_order: number
      status: TaskStatus; is_hidden: boolean; rejection_reason: string | null; preview_attachment_id: number | null
    }>) => api.patch<{ status: string; task: TaskRow }>(`${base}/tasks/${taskId}`, body),
    move: (taskId: number, body: { target_board_id: number; target_column_id: number }) =>
      api.post<{ status: string; task: TaskRow }>(`${base}/tasks/${taskId}/move`, body),
    delete: (taskId: number) => api.delete<{ status: string }>(`${base}/tasks/${taskId}`),
    reorder: (boardId: number, columns: Record<number, number[]>) =>
      api.post<{ status: string }>(`${base}/boards/${boardId}/tasks/reorder`, { columns }),
    reject: (taskId: number, reason: string) => api.post<{ status: string; task: TaskRow }>(`${base}/tasks/${taskId}/reject`, { reason }),
    toggleHidden: (taskId: number) => api.post<{ status: string; is_hidden: boolean }>(`${base}/tasks/${taskId}/toggle-hidden`, {}),
    attachments: {
      list: (taskId: number) => api.get<{ attachments: TaskAttachment[] }>(`${base}/tasks/${taskId}/attachments`),
      upload: (taskId: number, file: File, isPreview = false) => {
        const fd = new FormData()
        fd.append("file", file)
        fd.append("is_preview", String(isPreview))
        return api.postForm<{ status: string; attachment: TaskAttachment }>(`${base}/tasks/${taskId}/attachments`, fd)
      },
      delete: (taskId: number, attachmentId: number) => api.delete<{ status: string }>(`${base}/tasks/${taskId}/attachments/${attachmentId}`),
    },
    activity: (taskId: number) => api.get<{ activity: TaskActivity[] }>(`${base}/tasks/${taskId}/activity`),
  },

  comments: {
    list: (taskId: number) => api.get<{ comments: TaskComment[] }>(`${base}/tasks/${taskId}/comments`),
    add: (taskId: number, content: string) =>
      api.post<{ status: string; comment: TaskComment }>(`${base}/tasks/${taskId}/comments`, { content }),
    delete: (taskId: number, commentId: number) =>
      api.delete<{ status: string }>(`${base}/tasks/${taskId}/comments/${commentId}`),
  },

  assigneeCandidates: () => api.get<AssigneeCandidates>(`${base}/assignee-candidates`),
}
