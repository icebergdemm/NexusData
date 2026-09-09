import { lazy, Suspense } from "react"
import { Navigate, Route, Routes } from "react-router-dom"
import { AlertTriangle, RotateCw } from "lucide-react"
import { AppShell } from "@/components/layout/AppShell"
import { OnboardingModal } from "@/components/layout/OnboardingModal"
import { useCurrentAdmin } from "@/lib/auth"
import { ApiError } from "@/lib/api"
import { TicketsListPage } from "@/pages/tickets"

// Keep the ticket inbox on the critical path and split every unrelated route.
// A direct /tickets visit now downloads the shell + inbox instead of the whole
// admin application (charts, replay player, task board, document editor, etc.).
const DashboardPage = lazy(() => import("@/pages/dashboard").then(({ DashboardPage }) => ({ default: DashboardPage })))
const RoundStatusPage = lazy(() => import("@/pages/round-status").then(({ RoundStatusPage }) => ({ default: RoundStatusPage })))
const DispatchPage = lazy(() => import("@/pages/dispatch").then(({ DispatchPage }) => ({ default: DispatchPage })))
const WorldOverviewPage = lazy(() => import("@/pages/world-overview").then(({ WorldOverviewPage }) => ({ default: WorldOverviewPage })))
const PlayersListPage = lazy(() => import("@/pages/players").then(({ PlayersListPage }) => ({ default: PlayersListPage })))
const PlayerDetailPage = lazy(() => import("@/pages/players/detail").then(({ PlayerDetailPage }) => ({ default: PlayerDetailPage })))
const ModerationPage = lazy(() => import("@/pages/moderation").then(({ ModerationPage }) => ({ default: ModerationPage })))
const PunishmentsPage = lazy(() => import("@/pages/punishments").then(({ PunishmentsPage }) => ({ default: PunishmentsPage })))
const ModerationTemplatesPage = lazy(() => import("@/pages/moderation-templates").then(({ ModerationTemplatesPage }) => ({ default: ModerationTemplatesPage })))
const AnalyticsPage = lazy(() => import("@/pages/analytics").then(({ AnalyticsPage }) => ({ default: AnalyticsPage })))
const TicketDetailPage = lazy(() => import("@/pages/tickets/detail").then(({ TicketDetailPage }) => ({ default: TicketDetailPage })))
const RatingsPage = lazy(() => import("@/pages/ratings").then(({ RatingsPage }) => ({ default: RatingsPage })))
const RatingSnapshotViewPage = lazy(() => import("@/pages/ratings/snapshot-view").then(({ RatingSnapshotViewPage }) => ({ default: RatingSnapshotViewPage })))
const RewardsConfigPage = lazy(() => import("@/pages/ratings/rewards").then(({ RewardsConfigPage }) => ({ default: RewardsConfigPage })))
const SubProjectsListPage = lazy(() => import("@/pages/sub-projects").then(({ SubProjectsListPage }) => ({ default: SubProjectsListPage })))
const SubProjectDetailPage = lazy(() => import("@/pages/sub-projects/detail").then(({ SubProjectDetailPage }) => ({ default: SubProjectDetailPage })))
const SubProjectFormPage = lazy(() => import("@/pages/sub-projects/form").then(({ SubProjectFormPage }) => ({ default: SubProjectFormPage })))
const SubProjectCategoriesPage = lazy(() => import("@/pages/sub-projects/categories").then(({ SubProjectCategoriesPage }) => ({ default: SubProjectCategoriesPage })))
const SubProjectsPolygonListPage = lazy(() => import("@/pages/sub-projects-polygon").then(({ SubProjectsPolygonListPage }) => ({ default: SubProjectsPolygonListPage })))
const SubProjectsPolygonDetailPage = lazy(() => import("@/pages/sub-projects-polygon/detail").then(({ SubProjectsPolygonDetailPage }) => ({ default: SubProjectsPolygonDetailPage })))
const SubCheckerPage = lazy(() => import("@/pages/sub-checker").then(({ SubCheckerPage }) => ({ default: SubCheckerPage })))
const ReplaysListPage = lazy(() => import("@/pages/replays").then(({ ReplaysListPage }) => ({ default: ReplaysListPage })))
const ReplayPlayerPage = lazy(() => import("@/pages/replays/player").then(({ ReplayPlayerPage }) => ({ default: ReplayPlayerPage })))
const RoundDetailPage = lazy(() => import("@/pages/replays/round-detail").then(({ RoundDetailPage }) => ({ default: RoundDetailPage })))
const SessionsPage = lazy(() => import("@/pages/sessions").then(({ SessionsPage }) => ({ default: SessionsPage })))
const AuditPage = lazy(() => import("@/pages/audit").then(({ AuditPage }) => ({ default: AuditPage })))
const ErrorLogPage = lazy(() => import("@/pages/error-log").then(({ ErrorLogPage }) => ({ default: ErrorLogPage })))
const PerfMonitorPage = lazy(() => import("@/pages/perf-monitor").then(({ PerfMonitorPage }) => ({ default: PerfMonitorPage })))
const LabelCategoriesPage = lazy(() => import("@/pages/label-categories").then(({ LabelCategoriesPage }) => ({ default: LabelCategoriesPage })))
const UsersPage = lazy(() => import("@/pages/users").then(({ UsersPage }) => ({ default: UsersPage })))
const UserDetailPage = lazy(() => import("@/pages/users/detail").then(({ UserDetailPage }) => ({ default: UserDetailPage })))
const KitsPage = lazy(() => import("@/pages/kits").then(({ KitsPage }) => ({ default: KitsPage })))
const ServerPrivilegesPage = lazy(() => import("@/pages/server-privileges").then(({ ServerPrivilegesPage }) => ({ default: ServerPrivilegesPage })))
const DiscordBotPage = lazy(() => import("@/pages/discord-bot").then(({ DiscordBotPage }) => ({ default: DiscordBotPage })))
const AdministrationPage = lazy(() => import("@/pages/administration").then(({ AdministrationPage }) => ({ default: AdministrationPage })))
const AICenterPage = lazy(() => import("@/pages/ai-center").then(({ AICenterPage }) => ({ default: AICenterPage })))
const LogsPage = lazy(() => import("@/pages/logs").then(({ LogsPage }) => ({ default: LogsPage })))
const SoundsPage = lazy(() => import("@/pages/sounds").then(({ SoundsPage }) => ({ default: SoundsPage })))
const RespawnDollsPage = lazy(() => import("@/pages/respawn-dolls").then(({ RespawnDollsPage }) => ({ default: RespawnDollsPage })))
const AchievementsPage = lazy(() => import("@/pages/achievements").then(({ AchievementsPage }) => ({ default: AchievementsPage })))
const DocumentsPage = lazy(() => import("@/pages/documents").then(({ DocumentsPage }) => ({ default: DocumentsPage })))
const DepartmentsPage = lazy(() => import("@/pages/departments").then(({ DepartmentsPage }) => ({ default: DepartmentsPage })))
const DepartmentDetailPage = lazy(() => import("@/pages/departments/detail").then(({ DepartmentDetailPage }) => ({ default: DepartmentDetailPage })))
const TasksListPage = lazy(() => import("@/pages/tasks").then(({ TasksListPage }) => ({ default: TasksListPage })))
const TaskBoardPage = lazy(() => import("@/pages/tasks/board").then(({ TaskBoardPage }) => ({ default: TaskBoardPage })))
const DocumentEditorPage = lazy(() =>
  import("@/pages/documents/OnlyOfficeEditorPage").then((m) => ({ default: m.OnlyOfficeEditorPage }))
)

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { isError, error, refetch } = useCurrentAdmin()

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
      {/* Route queries start while /me is still loading, eliminating the
          authentication -> page-data network waterfall. */}
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
          <Route path="moderation-templates" element={<ModerationTemplatesPage />} />
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
          <Route path="users/:adminId" element={<UserDetailPage />} />
          <Route path="administration" element={<AdministrationPage />} />
          <Route path="ai-center" element={<AICenterPage />} />
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
