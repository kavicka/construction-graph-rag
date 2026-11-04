import { AppSidebar } from '@/components/app-sidebar'
import { LogoMark } from '@/components/logo'
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
import { lazy, Suspense, useEffect, useState } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router'
import LoginPage from './pages/login'

// routes load on demand — the graph (force-graph), PDF viewer and markdown stay out of the first chunk
const AttendancePage = lazy(() => import('./pages/attendance'))
const ChatPage = lazy(() => import('./pages/chat'))
const DocumentDetailPage = lazy(() => import('./pages/document-detail'))
const DocumentsPage = lazy(() => import('./pages/documents'))
const GraphPage = lazy(() => import('./pages/graph'))
const MyTasksPage = lazy(() => import('./pages/my-tasks'))
const ReportsPage = lazy(() => import('./pages/reports'))
const SettingsPage = lazy(() => import('./pages/settings'))
const StatusPage = lazy(() => import('./pages/status'))
const TaskImportPage = lazy(() => import('./pages/task-import'))
const TaskImportDetailPage = lazy(() => import('./pages/task-import-detail'))
const TasksBoardPage = lazy(() => import('./pages/tasks-board'))
const TeamPage = lazy(() => import('./pages/team'))
const TracesPage = lazy(() => import('./pages/traces'))
const UsersPage = lazy(() => import('./pages/users'))
const WorkerAskPage = lazy(() => import('./pages/worker-ask'))

/** Placeholder while a route chunk loads: the page-header rhythm, so nothing jumps. */
function PageFallback() {
  return (
    <div className="space-y-6" aria-busy="true" aria-label="Načítavam stránku">
      <Skeleton className="h-8 w-48" />
      <Skeleton className="h-64 w-full" />
    </div>
  )
}

/** group of workers signed in with a PIN — they only see attendance */
const WORKER_GROUP = 'pracovnici'

export default function App() {
  const queryClient = useQueryClient()
  const { data: me, isPending } = useQuery({ queryKey: ['me'], queryFn: fetchMe })
  // the task board (Trello-like) uses the full width; other pages keep a readable max-w-6xl
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
          {/* min-w-0: the wide board scrolls inside, the page never overflows */}
          <SidebarInset className="min-w-0">
            <MobileBar />
            <main
              className={cn(
                'mx-auto w-full min-w-0 flex-1 px-4 py-6 md:px-8 md:py-8',
                !fullWidth && 'max-w-6xl',
              )}
            >
              <Suspense fallback={<PageFallback />}>
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
              </Suspense>
            </main>
          </SidebarInset>
        </SidebarProvider>
      )}
      <Toaster />
    </TooltipProvider>
  )
}

/** Bar on phones only, where the sidebar is hidden. The page title lives in the page heading, not here. */
function MobileBar() {
  return (
    <header className="sticky top-0 z-40 flex h-14 shrink-0 items-center gap-2 border-b bg-background/90 px-4 backdrop-blur-sm md:hidden">
      <SidebarTrigger className="-ml-1" />
      <Separator orientation="vertical" className="mr-1 data-[orientation=vertical]:h-4" />
      <LogoMark />
      <span className="font-display text-base font-medium">rag</span>
    </header>
  )
}

type WorkerTab = 'report' | 'tasks' | 'ask'

/** Worker on a phone: no sidebar — daily report, task board and questions about their own data. */
function WorkerShell({ name, onLogout }: { name: string; onLogout: () => void }) {
  const [tab, setTab] = useState<WorkerTab>('report')
  return (
    <div className="min-h-svh bg-background">
      <header className="sticky top-0 z-40 flex h-14 items-center gap-3 border-b bg-background/90 px-4 backdrop-blur-sm">
        <LogoMark />
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
        <Suspense fallback={<Skeleton className="h-64" />}>
          {tab === 'report' && <AttendancePage isAdmin={false} />}
          {tab === 'tasks' && <MyTasksPage />}
          {tab === 'ask' && <WorkerAskPage />}
        </Suspense>
      </main>
    </div>
  )
}
