import { STATUS_COLORS, STATUS_LABELS, type TaskRow, type TaskStatus } from "./api"

export function getPublicTaskStatusBadge(status: TaskStatus): { label: string; color: string } | null {
  if (status === "testing") return { label: STATUS_LABELS.testing, color: STATUS_COLORS.testing }
  if (status === "in_dev_queue" || status === "in_progress") {
    return { label: "Передано в разработку", color: "#3B82F6" }
  }
  return null
}

export function splitReviewTasks(tasks: TaskRow[]) {
  return { full: tasks.slice(0, 20), compact: tasks.slice(20) }
}
