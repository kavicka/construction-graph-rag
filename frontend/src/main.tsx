import { MutationCache, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from 'next-themes'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import { toast } from 'sonner'
import App from './App'
import { ApiError, errorMessage } from './lib/errors'
import './index.css'

declare module '@tanstack/react-query' {
  interface Register {
    mutationMeta: {
      /** toast headline; the error message becomes the description */
      errorTitle?: string
      /** the mutation shows its own error UI */
      silentError?: boolean
    }
  }
}

// One error toast for every mutation; per-mutation onError only handles rollback or field errors.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // a 4xx will not fix itself — show the error state at once instead of retrying three times
      retry: (failures, error) => !(error instanceof ApiError && error.status < 500) && failures < 3,
    },
  },
  mutationCache: new MutationCache({
    onError: (error, _variables, _context, mutation) => {
      const meta = mutation.options.meta
      if (meta?.silentError) return
      if (meta?.errorTitle) toast.error(meta.errorTitle, { description: errorMessage(error) })
      else toast.error(errorMessage(error))
    },
  }),
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem storageKey="theme" disableTransitionOnChange>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>,
)
