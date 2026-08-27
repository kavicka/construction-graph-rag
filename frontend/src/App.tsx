import { Toaster } from '@/components/ui/sonner'
import { cn } from '@/lib/utils'
import { NavLink, Navigate, Route, Routes } from 'react-router'
import ChatPage from './pages/chat'
import DocumentDetailPage from './pages/document-detail'
import DocumentsPage from './pages/documents'
import GraphPage from './pages/graph'
import StatusPage from './pages/status'

const NAV = [
  { to: '/status', label: 'Stav' },
  { to: '/chat', label: 'Chat' },
  { to: '/documents', label: 'Dokumenty' },
  { to: '/graph', label: 'Graf' },
]

export default function App() {
  return (
    <div className="min-h-svh bg-background">
      <header className="border-b">
        <div className="mx-auto flex h-14 max-w-5xl items-center gap-6 px-4">
          <span className="font-semibold tracking-tight">rag</span>
          <nav className="flex gap-1">
            {NAV.map(({ to, label }) => (
              <NavLink
                key={to}
                to={to}
                className={({ isActive }) =>
                  cn(
                    'rounded-md px-3 py-1.5 text-sm transition-colors',
                    isActive
                      ? 'bg-secondary font-medium text-foreground'
                      : 'text-muted-foreground hover:text-foreground',
                  )
                }
              >
                {label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-4 py-8">
        <Routes>
          <Route path="/" element={<Navigate to="/status" replace />} />
          <Route path="/status" element={<StatusPage />} />
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/documents" element={<DocumentsPage />} />
          <Route path="/documents/:id" element={<DocumentDetailPage />} />
          <Route path="/graph" element={<GraphPage />} />
        </Routes>
      </main>
      <Toaster />
    </div>
  )
}
