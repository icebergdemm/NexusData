import { useCallback, useState } from "react"
import { Link, Outlet, useLocation } from "react-router-dom"
import { ArrowLeft, LogOut, PanelLeftClose, PanelLeftOpen, PanelsTopLeft } from "lucide-react"
import {
  Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupContent,
  SidebarGroupLabel, SidebarHeader, SidebarInset, SidebarMenu, SidebarMenuButton,
  SidebarMenuItem, SidebarProvider, SidebarRail, SidebarTrigger, useSidebar,
} from "@/components/ui/sidebar"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { NAV_GROUPS } from "@/components/layout/nav-config"
import { canView, useCurrentAdmin } from "@/lib/auth"
import { NotificationBell } from "@/components/layout/NotificationBell"
import { V2ThemeToggle } from "@/components/layout/V2ThemeToggle"
import { IS_V2, buildUiVersionHref } from "@/lib/ui-version"
import { cn } from "@/lib/utils"

const V2_SIDEBAR_STORAGE_KEY = "nexusdata-v2-sidebar-open"

function isNavItemActive(pathname: string, destination: string) {
  if (destination === "/") return pathname === destination
  return pathname === destination || pathname.startsWith(`${destination}/`)
}

function readV2SidebarState() {
  if (!IS_V2) return true
  try {
    return window.localStorage.getItem(V2_SIDEBAR_STORAGE_KEY) !== "false"
  } catch {
    return true
  }
}

function V2SidebarTrigger() {
  const { isMobile, openMobile, state, toggleSidebar } = useSidebar()
  const isExpanded = isMobile ? openMobile : state === "expanded"
  const action = isExpanded ? "Свернуть боковую панель" : "Развернуть боковую панель"

  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      data-slot="v2-sidebar-trigger"
      aria-label={action}
      aria-expanded={isExpanded}
      title={`${action} (Ctrl/⌘ + B)`}
      className="group/sidebar-toggle relative size-8 overflow-hidden"
      onClick={toggleSidebar}
    >
      <PanelLeftClose
        aria-hidden="true"
        className={cn(
          "absolute size-4 transition-[opacity,transform] duration-200 ease-out",
          isExpanded
            ? "translate-x-0 opacity-100"
            : "-translate-x-2 opacity-0",
        )}
      />
      <PanelLeftOpen
        aria-hidden="true"
        className={cn(
          "absolute size-4 transition-[opacity,transform] duration-200 ease-out",
          isExpanded
            ? "translate-x-2 opacity-0"
            : "translate-x-0 opacity-100",
        )}
      />
      <span className="sr-only">{action}</span>
    </Button>
  )
}

export function AppShell() {
  const { data: admin } = useCurrentAdmin()
  const location = useLocation()
  const versionHref = buildUiVersionHref(IS_V2 ? "classic" : "v2", location)
  const [v2SidebarOpen, setV2SidebarOpen] = useState(readV2SidebarState)
  const handleV2SidebarOpenChange = useCallback((open: boolean) => {
    setV2SidebarOpen(open)
    try {
      window.localStorage.setItem(V2_SIDEBAR_STORAGE_KEY, String(open))
    } catch {
      // The interface still works when browser storage is unavailable.
    }
  }, [])

  return (
    <SidebarProvider
      open={IS_V2 ? v2SidebarOpen : undefined}
      onOpenChange={IS_V2 ? handleV2SidebarOpenChange : undefined}
    >
      <Sidebar collapsible="icon">
        <SidebarHeader>
          <div data-slot={IS_V2 ? "v2-sidebar-brand" : undefined} className="flex items-center gap-2 px-2 py-1.5">
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
            <span
              data-v2-sidebar-label={IS_V2 ? "brand" : undefined}
              className="font-semibold text-sm group-data-[collapsible=icon]:hidden"
            >
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
                    {visibleItems.map((item) => {
                      const isActive = IS_V2
                        ? isNavItemActive(location.pathname, item.to)
                        : location.pathname === item.to

                      return (
                        <SidebarMenuItem key={item.to}>
                          <SidebarMenuButton
                            asChild
                            isActive={isActive}
                            tooltip={IS_V2 ? {
                              sideOffset: 10,
                              children: (
                                <span data-slot="v2-sidebar-tooltip" className="grid gap-0.5">
                                  <span className="font-medium">{item.label}</span>
                                  <span className="text-[10px] uppercase tracking-[0.12em] opacity-60">
                                    {group.label}
                                  </span>
                                </span>
                              ),
                            } : item.label}
                          >
                            <Link
                              to={item.to}
                              data-v2-nav-item={IS_V2 ? item.to : undefined}
                              aria-label={IS_V2 ? item.label : undefined}
                              aria-current={IS_V2 && isActive ? "page" : undefined}
                            >
                              <item.icon />
                              <span data-v2-sidebar-label={IS_V2 ? "item" : undefined}>{item.label}</span>
                            </Link>
                          </SidebarMenuButton>
                        </SidebarMenuItem>
                      )
                    })}
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
                  <span data-v2-sidebar-label={IS_V2 ? "item" : undefined}>
                    {IS_V2 ? "Старый интерфейс" : "Интерфейс V2"}
                  </span>
                </a>
              </SidebarMenuButton>
            </SidebarMenuItem>
            <SidebarMenuItem>
              <form action="/logout" method="post">
                <input type="hidden" name="csrf_token" value={admin?.csrf_token ?? ""} />
                <SidebarMenuButton asChild tooltip={IS_V2 ? "Выйти из NexusData" : undefined}>
                  <button type="submit" className="w-full" aria-label={IS_V2 ? "Выйти из NexusData" : undefined}>
                    <LogOut />
                    <span data-v2-sidebar-label={IS_V2 ? "item" : undefined}>Выйти</span>
                  </button>
                </SidebarMenuButton>
              </form>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarFooter>
        {IS_V2 && (
          <SidebarRail
            aria-label="Свернуть или развернуть боковую панель"
            title="Свернуть или развернуть боковую панель"
            tabIndex={0}
          />
        )}
      </Sidebar>

      <SidebarInset>
        <header data-slot="app-header" className="flex h-14 shrink-0 items-center gap-2 border-b px-4">
          {IS_V2 ? <V2SidebarTrigger /> : <SidebarTrigger />}
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
