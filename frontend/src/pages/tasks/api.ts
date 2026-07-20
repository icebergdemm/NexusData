import { api } from "@/lib/api"

export type TaskPriority = "low" | "medium" | "high" | "urgent"

export const PRIORITY_LABELS: Record<TaskPriority, string> = {
  low: "Низкий",
  medium: "Средний",
  high: "Высокий",
  urgent: "Срочно",
}

export const PRIORITY_COLORS: Record<TaskPriority, string> = {
  low: "#5A646E",
  medium: "#3B82F6",
  high: "#F59E0B",
  urgent: "#EF4444",
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
}

export interface AssigneeCandidates {
  admins: { id: number; name: string | null }[]
  departments: { id: number; name: string; icon: string | null }[]
}

const base = "/api/v1/admin/tasks"

export const tasksApi = {
  boards: {
    list: () => api.get<{ boards: TaskBoardRow[]; can_manage_boards: boolean; can_create: boolean }>(`${base}/boards`),
    get: (boardId: number) => api.get<TaskBoardDetail>(`${base}/boards/${boardId}`),
    create: (body: { name: string; description?: string; icon?: string; color?: string; department_id?: number | null }) =>
      api.post<{ status: string; board: TaskBoardRow }>(`${base}/boards`, body),
    update: (boardId: number, body: { name?: string; description?: string; icon?: string; color?: string; department_id?: number | null }) =>
      api.patch<{ status: string; board: TaskBoardRow }>(`${base}/boards/${boardId}`, body),
    archive: (boardId: number) => api.delete<{ status: string }>(`${base}/boards/${boardId}`),
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
    }>) => api.patch<{ status: string; task: TaskRow }>(`${base}/tasks/${taskId}`, body),
    delete: (taskId: number) => api.delete<{ status: string }>(`${base}/tasks/${taskId}`),
    reorder: (boardId: number, columns: Record<number, number[]>) =>
      api.post<{ status: string }>(`${base}/boards/${boardId}/tasks/reorder`, { columns }),
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
