import { lazy, Suspense } from "react"
import { Navigate, Route, Routes } from "react-router-dom"
import { AppShell } from "@/components/layout/AppShell"
import { useCurrentAdmin } from "@/lib/auth"
import { DashboardPage } from "@/pages/dashboard"
import { RoundStatusPage } from "@/pages/round-status"
import { DispatchPage } from "@/pages/dispatch"
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
import { ReplaysListPage } from "@/pages/replays"
import { ReplayPlayerPage } from "@/pages/replays/player"
import { SessionsPage } from "@/pages/sessions"
import { AuditPage } from "@/pages/audit"
import { LabelCategoriesPage } from "@/pages/label-categories"
import { UsersPage } from "@/pages/users"
import { KitsPage } from "@/pages/kits"
import { ServerPrivilegesPage } from "@/pages/server-privileges"
import { DiscordBotPage } from "@/pages/discord-bot"
import { AdministrationPage } from "@/pages/administration"
import { LogsPage } from "@/pages/logs"
import { DocumentsPage } from "@/pages/documents"

// The rich text editor pulls in the full Tiptap extension set — code-split it
// so every other page doesn't pay for that weight on first load.
const DocumentEditorPage = lazy(() =>
  import("@/pages/documents/editor").then((m) => ({ default: m.DocumentEditorPage }))
)

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { isLoading, isError } = useCurrentAdmin()

  if (isLoading) {
    return <div className="flex h-screen items-center justify-center text-muted-foreground">Загрузка…</div>
  }
  if (isError) {
    // The api client already redirects to /login on 401; this is a fallback.
    return null
  }
  return <>{children}</>
}

export default function App() {
  return (
    <RequireAuth>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<DashboardPage />} />
          <Route path="round-status" element={<RoundStatusPage />} />
          <Route path="dispatch" element={<DispatchPage />} />
          <Route path="players" element={<PlayersListPage />} />
          <Route path="players/:playerId" element={<PlayerDetailPage />} />
          <Route path="label-categories" element={<LabelCategoriesPage />} />
          <Route path="moderation" element={<ModerationPage />} />
          <Route path="logs" element={<LogsPage />} />
          <Route path="punishments" element={<PunishmentsPage />} />
          <Route path="replays" element={<ReplaysListPage />} />
          <Route path="replays/:replayId" element={<ReplayPlayerPage />} />
          <Route path="sub-projects" element={<SubProjectsListPage />} />
          <Route path="sub-projects/categories" element={<SubProjectCategoriesPage />} />
          <Route path="sub-projects/new" element={<SubProjectFormPage />} />
          <Route path="sub-projects/:projectId" element={<SubProjectDetailPage />} />
          <Route path="sub-projects/:projectId/edit" element={<SubProjectFormPage />} />
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
          <Route path="analytics" element={<AnalyticsPage />} />
          <Route path="documents" element={<DocumentsPage />} />
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
