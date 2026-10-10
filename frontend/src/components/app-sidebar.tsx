import { LogoMark } from '@/components/logo'
import { ThemeToggle } from '@/components/theme-toggle'
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  SidebarTrigger,
  useSidebar,
} from '@/components/ui/sidebar'
import { WorkerAvatar } from '@/components/wf/bits'
import { Logout03Icon } from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import type { NavGroup, NavItem } from '@/lib/nav'
import { NavLink, useLocation } from 'react-router'

function isActive(item: NavItem, pathname: string): boolean {
  if (item.exact) return pathname === item.to
  return pathname === item.to || pathname.startsWith(`${item.to}/`)
}

export function AppSidebar({
  groups,
  user,
  onLogout,
}: {
  groups: NavGroup[]
  user: { name: string; email: string } | null
  onLogout: () => void
}) {
  const { pathname } = useLocation()
  const { isMobile, setOpenMobile } = useSidebar()

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem className="flex items-center gap-1 group-data-[collapsible=icon]:flex-col">
            <SidebarMenuButton size="lg" asChild tooltip="rag">
              <NavLink to="/" onClick={() => isMobile && setOpenMobile(false)}>
                <span className="flex size-8 shrink-0 items-center justify-center">
                  {/* SidebarMenuButton vynucuje [&_svg]:size-4, logo má iný pomer strán */}
                  <LogoMark className="h-[18px]! w-auto!" />
                </span>
                <span className="font-display text-lg font-medium">rag</span>
              </NavLink>
            </SidebarMenuButton>
            {/* na mobile sidebar zatvára sheet, prepínač je v lište */}
            {!isMobile && <SidebarTrigger className="shrink-0 text-muted-foreground" />}
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      <SidebarContent>
        {groups.map((group) => (
          <SidebarGroup key={group.label}>
            <SidebarGroupLabel className="label-mono text-[11px] uppercase tracking-wider">
              {group.label}
            </SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {group.items.map((item) => (
                  <SidebarMenuItem key={item.to}>
                    <SidebarMenuButton
                      asChild
                      isActive={isActive(item, pathname)}
                      tooltip={item.label}
                      className="data-[active=true]:text-primary"
                    >
                      <NavLink to={item.to} onClick={() => isMobile && setOpenMobile(false)}>
                        <HugeiconsIcon icon={item.icon} strokeWidth={1.8} />
                        <span>{item.label}</span>
                      </NavLink>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        ))}
      </SidebarContent>

      <SidebarFooter>
        <SidebarMenu>
          <SidebarMenuItem className="flex items-center gap-1 group-data-[collapsible=icon]:flex-col">
            {user ? (
              <div className="flex min-w-0 flex-1 items-center gap-2 px-1 group-data-[collapsible=icon]:hidden">
                <WorkerAvatar name={user.name} size="md" />
                <div className="min-w-0 leading-tight">
                  <p className="truncate text-sm font-medium">{user.name}</p>
                  <p className="label-mono truncate text-[11px] text-muted-foreground">
                    {user.email}
                  </p>
                </div>
              </div>
            ) : (
              <span className="label-mono flex-1 px-2 text-[11px] text-muted-foreground group-data-[collapsible=icon]:hidden">
                lokálny režim
              </span>
            )}
            <ThemeToggle />
            {user && (
              <SidebarMenuButton
                tooltip="Odhlásiť"
                onClick={onLogout}
                className="size-8 shrink-0 justify-center p-0"
                aria-label="Odhlásiť"
              >
                <HugeiconsIcon icon={Logout03Icon} strokeWidth={1.8} />
              </SidebarMenuButton>
            )}
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}
