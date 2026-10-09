import { AppSidebar } from '@/components/app-sidebar'
import { ThemeToggle } from '@/components/theme-toggle'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { SidebarInset, SidebarProvider, SidebarTrigger } from '@/components/ui/sidebar'
import { Skeleton } from '@/components/ui/skeleton'
import { Toaster } from '@/components/ui/sonner'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { TooltipProvider } from '@/components/ui/tooltip'
import { WorkerAvatar } from '@/components/wf/bits'
import { fetchMe, logout } from '@/lib/api'
import { navGroups } from '@/lib/nav'
import { cn } from '@/lib/utils'
import { Logout03Icon } from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router'
import AttendancePage from './pages/attendance'
import ChatPage from './pages/chat'
import DocumentDetailPage from './pages/document-detail'
import DocumentsPage from './pages/documents'
import GraphPage from './pages/graph'
import LoginPage from './pages/login'
import MyTasksPage from './pages/my-tasks'
import ReportsPage from './pages/reports'
import SettingsPage from './pages/settings'
import StatusPage from './pages/status'
import TaskImportPage from './pages/task-import'
import TaskImportDetailPage from './pages/task-import-detail'
import TasksBoardPage from './pages/tasks-board'
import TeamPage from './pages/team'
import TracesPage from './pages/traces'
import UsersPage from './pages/users'
import WorkerAskPage from './pages/worker-ask'

/** skupina pracovníkov prihlásených PIN-om — vidia len dochádzku */
const WORKER_GROUP = 'pracovnici'

export default function App() {
  const queryClient = useQueryClient()
  const { data: me, isPending } = useQuery({ queryKey: ['me'], queryFn: fetchMe })
  // nástenka úloh (ako Trello) využíva celú šírku; ostatné stránky majú čitateľný max-w-6xl
  const fullWidth = useLocation().pathname.replace(/\/$/, '') === '/tasks'

  useEffect(() => {
    const onUnauthorized = () => void queryClient.invalidateQueries({ queryKey: ['me'] })
    window.addEventListener('rag:unauthorized', onUnauthorized)
    return () => window.removeEventListener('rag:unauthorized', onUnauthorized)
  }, [queryClient])

  if (isPending) return <Skeleton className="m-8 h-40" />
  if (me?.auth_enabled && !me.user) {
    return (
      <>
        <LoginPage />
        <Toaster />
      </>
    )
  }
  const doLogout = () => void logout().then(() => queryClient.resetQueries())
  const isWorker = !!me?.user?.groups.includes(WORKER_GROUP)
  const isAdmin = !me?.auth_enabled || me.user?.role === 'admin'

  return (
    <TooltipProvider delayDuration={300}>
      {isWorker && me?.user ? (
        <WorkerShell name={me.user.name} onLogout={doLogout} />
      ) : (
        <SidebarProvider>
          <AppSidebar
            groups={navGroups({ isAdmin })}
            user={me?.user ? { name: me.user.name, email: me.user.email } : null}
            onLogout={doLogout}
          />
          {/* min-w-0: široká nástenka sa posúva vo vnútri, stránka nepretečie */}
          <SidebarInset className="min-w-0">
            <MobileBar />
            <main
              className={cn(
                'mx-auto w-full min-w-0 flex-1 px-4 py-6 md:px-8 md:py-8',
                !fullWidth && 'max-w-6xl',
              )}
            >
              <Routes>
                <Route path="/" element={<Navigate to={isAdmin ? '/reports' : '/status'} replace />} />
                <Route path="/status" element={<StatusPage />} />
                <Route path="/chat" element={<ChatPage />} />
                <Route path="/documents" element={<DocumentsPage />} />
                <Route path="/documents/:id" element={<DocumentDetailPage />} />
                <Route path="/graph" element={<GraphPage />} />
                <Route path="/traces" element={<TracesPage />} />
                <Route path="/settings" element={<SettingsPage />} />
                {isAdmin && (
                  <>
                    <Route path="/reports" element={<ReportsPage />} />
                    <Route path="/tasks" element={<TasksBoardPage />} />
                    <Route path="/tasks/import" element={<TaskImportPage />} />
                    <Route path="/tasks/import/:id" element={<TaskImportDetailPage />} />
                    <Route path="/attendance" element={<AttendancePage isAdmin />} />
                    <Route path="/team" element={<TeamPage />} />
                    <Route path="/users" element={<UsersPage />} />
                  </>
                )}
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </main>
          </SidebarInset>
        </SidebarProvider>
      )}
      <Toaster />
    </TooltipProvider>
  )
}

/** Lišta len na mobile, kde je sidebar schovaný. Názov stránky nesie nadpis stránky, nie lišta. */
function MobileBar() {
  return (
    <header className="sticky top-0 z-40 flex h-14 shrink-0 items-center gap-2 border-b bg-background/90 px-4 backdrop-blur-sm md:hidden">
      <SidebarTrigger className="-ml-1" />
      <Separator orientation="vertical" className="mr-1 data-[orientation=vertical]:h-4" />
      <span className="size-3 rounded-[3px] bg-primary" />
      <span className="font-display text-base font-medium">rag</span>
    </header>
  )
}

type WorkerTab = 'report' | 'tasks' | 'ask'

/** Pracovník na mobile: bez sidebaru — denný výpis, nástenka úloh a otázky na vlastné údaje. */
function WorkerShell({ name, onLogout }: { name: string; onLogout: () => void }) {
  const [tab, setTab] = useState<WorkerTab>('report')
  return (
    <div className="min-h-svh bg-background">
      <header className="sticky top-0 z-40 flex h-14 items-center gap-3 border-b bg-background/90 px-4 backdrop-blur-sm">
        <span className="size-3 rounded-[3px] bg-primary" />
        <WorkerAvatar name={name} />
        <span className="min-w-0 flex-1 truncate text-sm font-medium">{name}</span>
        <ThemeToggle />
        <Button variant="ghost" size="sm" onClick={onLogout} className="gap-1.5">
          <HugeiconsIcon icon={Logout03Icon} className="size-4" />
          Odhlásiť
        </Button>
      </header>
      <main className="mx-auto max-w-xl space-y-4 px-4 py-4">
        <Tabs value={tab} onValueChange={(v) => setTab(v as WorkerTab)}>
          <TabsList className="w-full">
            <TabsTrigger value="report" className="flex-1">
              Denný výpis
            </TabsTrigger>
            <TabsTrigger value="tasks" className="flex-1">
              Moje úlohy
            </TabsTrigger>
            <TabsTrigger value="ask" className="flex-1">
              Opýtaj sa
            </TabsTrigger>
          </TabsList>
        </Tabs>
        {tab === 'report' && <AttendancePage isAdmin={false} />}
        {tab === 'tasks' && <MyTasksPage />}
        {tab === 'ask' && <WorkerAskPage />}
      </main>
    </div>
  )
}
