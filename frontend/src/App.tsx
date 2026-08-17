import { lazy, Suspense } from "react"
import { Navigate, Route, Routes } from "react-router-dom"
import { AlertTriangle, RotateCw } from "lucide-react"
import { AppShell } from "@/components/layout/AppShell"
import { OnboardingModal } from "@/components/layout/OnboardingModal"
import { useCurrentAdmin } from "@/lib/auth"
import { ApiError } from "@/lib/api"
import { DashboardPage } from "@/pages/dashboard"
import { RoundStatusPage } from "@/pages/round-status"
import { DispatchPage } from "@/pages/dispatch"
import { WorldOverviewPage } from "@/pages/world-overview"
import { PlayersListPage } from "@/pages/players"
import { PlayerDetailPage } from "@/pages/players/detail"
import { ModerationPage } from "@/pages/moderation"
import { PunishmentsPage } from "@/pages/punishments"
import { AnalyticsPage } from "@/pages/analytics"
import { TicketsListPage } from "@/pages/tickets"
import { TicketDetailPage } from "@/pages/tickets/detail"
import { RatingsPage } from "@/pages/ratings"
import { RatingSnapshotViewPage } from "@/pages/ratings/snapshot-view"
import { RewardsConfigPage } from "@/pages/ratings/rewards"
import { SubProjectsListPage } from "@/pages/sub-projects"
import { SubProjectDetailPage } from "@/pages/sub-projects/detail"
import { SubProjectFormPage } from "@/pages/sub-projects/form"
import { SubProjectCategoriesPage } from "@/pages/sub-projects/categories"
import { SubProjectsPolygonListPage } from "@/pages/sub-projects-polygon"
import { SubProjectsPolygonDetailPage } from "@/pages/sub-projects-polygon/detail"
import { SubCheckerPage } from "@/pages/sub-checker"
import { ReplaysListPage } from "@/pages/replays"
import { ReplayPlayerPage } from "@/pages/replays/player"
import { RoundDetailPage } from "@/pages/replays/round-detail"
import { SessionsPage } from "@/pages/sessions"
import { AuditPage } from "@/pages/audit"
import { ErrorLogPage } from "@/pages/error-log"
import { PerfMonitorPage } from "@/pages/perf-monitor"
import { LabelCategoriesPage } from "@/pages/label-categories"
import { UsersPage } from "@/pages/users"
import { KitsPage } from "@/pages/kits"
import { ServerPrivilegesPage } from "@/pages/server-privileges"
import { DiscordBotPage } from "@/pages/discord-bot"
import { AdministrationPage } from "@/pages/administration"
import { LogsPage } from "@/pages/logs"
import { SoundsPage } from "@/pages/sounds"
import { RespawnDollsPage } from "@/pages/respawn-dolls"
import { AchievementsPage } from "@/pages/achievements"
import { DocumentsPage } from "@/pages/documents"
import { DepartmentsPage } from "@/pages/departments"
import { DepartmentDetailPage } from "@/pages/departments/detail"
import { TasksListPage } from "@/pages/tasks"
import { TaskBoardPage } from "@/pages/tasks/board"

// The ONLYOFFICE editor page dynamically loads Document Server's own script
// — code-split it so every other page doesn't pay for that on first load.
const DocumentEditorPage = lazy(() =>
  import("@/pages/documents/OnlyOfficeEditorPage").then((m) => ({ default: m.OnlyOfficeEditorPage }))
)

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { isLoading, isError, error, refetch } = useCurrentAdmin()

  if (isLoading) {
    return <div className="flex h-screen items-center justify-center text-muted-foreground">Загрузка…</div>
  }
  if (isError) {
    // The api client already hard-redirects to /login on 401 before this
    // ever renders — this only fires for network failures, 500s, or a 503
    // (maintenance/degraded, also caught globally by MaintenanceBanner).
    // Used to just `return null` (a blank white page) here.
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-3 p-4 text-center">
        <AlertTriangle className="size-8 text-destructive" />
        <div>
          <p className="font-medium">Не удалось связаться с сервером</p>
          <p className="mt-1 max-w-sm text-sm text-muted-foreground">
            {error instanceof ApiError ? error.message : "Проверьте соединение и попробуйте снова."}
          </p>
        </div>
        <button
          onClick={() => refetch()}
          className="mt-1 flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs font-medium hover:bg-accent"
        >
          <RotateCw className="size-3.5" /> Повторить
        </button>
      </div>
    )
  }
  return (
    <>
      <OnboardingModal />
      {children}
    </>
  )
}

export default function App() {
  return (
    <RequireAuth>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<DashboardPage />} />
          <Route path="round-status" element={<RoundStatusPage />} />
          <Route path="dispatch" element={<DispatchPage />} />
          <Route path="world-overview" element={<WorldOverviewPage />} />
          <Route path="players" element={<PlayersListPage />} />
          <Route path="players/:playerId" element={<PlayerDetailPage />} />
          <Route path="label-categories" element={<LabelCategoriesPage />} />
          <Route path="moderation" element={<ModerationPage />} />
          <Route path="logs" element={<LogsPage />} />
          <Route path="sounds" element={<SoundsPage />} />
          <Route path="respawn-dolls" element={<RespawnDollsPage />} />
          <Route path="achievements" element={<AchievementsPage />} />
          <Route path="punishments" element={<PunishmentsPage />} />
          <Route path="replays" element={<ReplaysListPage />} />
          <Route path="replays/rounds/:roundId" element={<RoundDetailPage />} />
          <Route path="replays/:replayId" element={<ReplayPlayerPage />} />
          <Route path="sub-projects" element={<SubProjectsListPage />} />
          <Route path="sub-projects/categories" element={<SubProjectCategoriesPage />} />
          <Route path="sub-projects/new" element={<SubProjectFormPage />} />
          <Route path="sub-projects/:projectId" element={<SubProjectDetailPage />} />
          <Route path="sub-projects/:projectId/edit" element={<SubProjectFormPage />} />
          <Route path="sub-projects-polygon" element={<SubProjectsPolygonListPage />} />
          <Route path="sub-projects-polygon/:projectId" element={<SubProjectsPolygonDetailPage />} />
          <Route path="sub-checker" element={<SubCheckerPage />} />
          <Route path="ratings" element={<RatingsPage />} />
          <Route path="ratings/rewards" element={<RewardsConfigPage />} />
          <Route path="ratings/snapshots/:filename" element={<RatingSnapshotViewPage />} />
          <Route path="discord-bot" element={<DiscordBotPage />} />
          <Route path="tickets" element={<TicketsListPage />} />
          <Route path="tickets/:ticketId" element={<TicketDetailPage />} />
          <Route path="users" element={<UsersPage />} />
          <Route path="administration" element={<AdministrationPage />} />
          <Route path="server-privileges" element={<ServerPrivilegesPage />} />
          <Route path="kits" element={<KitsPage />} />
          <Route path="sessions" element={<SessionsPage />} />
          <Route path="audit" element={<AuditPage />} />
          <Route path="error-log" element={<ErrorLogPage />} />
          <Route path="perf-monitor" element={<PerfMonitorPage />} />
          <Route path="analytics" element={<AnalyticsPage />} />
          <Route path="documents" element={<DocumentsPage />} />
          <Route path="tasks" element={<TasksListPage />} />
          <Route path="tasks/:boardId" element={<TaskBoardPage />} />
          <Route path="departments" element={<DepartmentsPage />} />
          <Route path="departments/:departmentId" element={<DepartmentDetailPage />} />
          <Route
            path="documents/:documentId"
            element={
              <Suspense fallback={<div className="p-6 text-sm text-muted-foreground">Загрузка редактора…</div>}>
                <DocumentEditorPage />
              </Suspense>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </RequireAuth>
  )
}
