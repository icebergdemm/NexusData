import { Link, Outlet, useLocation } from "react-router-dom"
import { LogOut } from "lucide-react"
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

export function AppShell() {
  const { data: admin } = useCurrentAdmin()
  const location = useLocation()

  return (
    <SidebarProvider>
      <Sidebar collapsible="icon">
        <SidebarHeader>
          <div className="flex items-center gap-2 px-2 py-1.5">
            <div className="flex h-7 w-7 items-center justify-center rounded-md bg-primary text-primary-foreground text-xs font-bold">
              ND
            </div>
            <span className="font-semibold text-sm group-data-[collapsible=icon]:hidden">
              NexusData
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
        <header className="flex h-14 shrink-0 items-center gap-2 border-b px-4">
          <SidebarTrigger />
          <Separator orientation="vertical" className="h-5" />
          <div className="flex-1" />
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
        <main className="min-w-0 flex-1 overflow-auto p-3 sm:p-6">
          <div key={location.pathname} className="animate-in fade-in slide-in-from-bottom-2 duration-300 ease-out">
            <Outlet />
          </div>
        </main>
      </SidebarInset>
    </SidebarProvider>
  )
}
