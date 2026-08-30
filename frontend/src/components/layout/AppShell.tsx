import { Link, Outlet, useLocation } from "react-router-dom"
import { ArrowLeft, LogOut, PanelsTopLeft } from "lucide-react"
import {
  Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupContent,
  SidebarGroupLabel, SidebarHeader, SidebarInset, SidebarMenu, SidebarMenuButton,
  SidebarMenuItem, SidebarProvider, SidebarTrigger,
} from "@/components/ui/sidebar"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Separator } from "@/components/ui/separator"
import { NAV_GROUPS } from "@/components/layout/nav-config"
import { canView, useCurrentAdmin } from "@/lib/auth"
import { NotificationBell } from "@/components/layout/NotificationBell"
import { V2ThemeToggle } from "@/components/layout/V2ThemeToggle"
import { IS_V2, buildUiVersionHref } from "@/lib/ui-version"
import { cn } from "@/lib/utils"

export function AppShell() {
  const { data: admin } = useCurrentAdmin()
  const location = useLocation()
  const versionHref = buildUiVersionHref(IS_V2 ? "classic" : "v2", location)

  return (
    <SidebarProvider>
      <Sidebar collapsible="icon">
        <SidebarHeader>
          <div className="flex items-center gap-2 px-2 py-1.5">
            <div
              className={cn(
                "flex h-7 w-7 items-center justify-center text-xs font-bold",
                IS_V2
                  ? "border border-sidebar-border bg-transparent text-sidebar-foreground"
                  : "rounded-md bg-primary text-primary-foreground",
              )}
            >
              {IS_V2 ? "V2" : "ND"}
            </div>
            <span className="font-semibold text-sm group-data-[collapsible=icon]:hidden">
              NexusData{IS_V2 && <span className="ml-1 text-sidebar-foreground/60">/ V2</span>}
            </span>
          </div>
        </SidebarHeader>
        <SidebarContent>
          {NAV_GROUPS.map((group) => {
            const visibleItems = group.items.filter((item) => {
              if (item.permission && !canView(admin, item.permission)) return false
              if (item.isVisible && !item.isVisible(admin)) return false
              return true
            })
            if (visibleItems.length === 0) return null
            return (
              <SidebarGroup key={group.label}>
                <SidebarGroupLabel>{group.label}</SidebarGroupLabel>
                <SidebarGroupContent>
                  <SidebarMenu>
                    {visibleItems.map((item) => (
                      <SidebarMenuItem key={item.to}>
                        <SidebarMenuButton asChild isActive={location.pathname === item.to} tooltip={item.label}>
                          <Link to={item.to}>
                            <item.icon />
                            <span>{item.label}</span>
                          </Link>
                        </SidebarMenuButton>
                      </SidebarMenuItem>
                    ))}
                  </SidebarMenu>
                </SidebarGroupContent>
              </SidebarGroup>
            )
          })}
        </SidebarContent>
        <SidebarFooter>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton
                asChild
                size="sm"
                tooltip={IS_V2 ? "Вернуться к старому интерфейсу" : "Открыть интерфейс V2"}
                className="text-sidebar-foreground/60 hover:text-sidebar-foreground"
              >
                <a href={versionHref}>
                  {IS_V2 ? <ArrowLeft /> : <PanelsTopLeft />}
                  <span>{IS_V2 ? "Старый интерфейс" : "Интерфейс V2"}</span>
                </a>
              </SidebarMenuButton>
            </SidebarMenuItem>
            <SidebarMenuItem>
              <form action="/logout" method="post">
                <input type="hidden" name="csrf_token" value={admin?.csrf_token ?? ""} />
                <SidebarMenuButton asChild>
                  <button type="submit" className="w-full">
                    <LogOut />
                    <span>Выйти</span>
                  </button>
                </SidebarMenuButton>
              </form>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarFooter>
      </Sidebar>

      <SidebarInset>
        <header data-slot="app-header" className="flex h-14 shrink-0 items-center gap-2 border-b px-4">
          <SidebarTrigger />
          <Separator orientation="vertical" className="h-5" />
          {IS_V2 && (
            <div className="hidden items-center gap-2 text-[10px] font-medium uppercase tracking-[0.18em] text-muted-foreground sm:flex">
              <span>Операторская консоль</span>
              <span aria-hidden="true">/</span>
              <span className="text-foreground">V2</span>
            </div>
          )}
          <div className="flex-1" />
          {IS_V2 && <V2ThemeToggle />}
          <NotificationBell />
          {admin && (
            <div className="flex items-center gap-2">
              <span className="hidden text-sm text-muted-foreground sm:inline">{admin.discord_username}</span>
              <Avatar className="h-7 w-7 shrink-0">
                <AvatarImage src={admin.discord_avatar ?? undefined} />
                <AvatarFallback>{admin.discord_username?.[0]?.toUpperCase() ?? "?"}</AvatarFallback>
              </Avatar>
            </div>
          )}
        </header>
        <main data-slot="app-content" className="min-w-0 flex-1 overflow-auto p-3 sm:p-6">
          <div key={location.pathname} className="animate-in fade-in slide-in-from-bottom-2 duration-300 ease-out">
            <Outlet />
          </div>
        </main>
      </SidebarInset>
    </SidebarProvider>
  )
}
