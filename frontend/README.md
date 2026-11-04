# Frontend

React 19 + TypeScript + Vite, Tailwind 4, shadcn/ui (Radix), TanStack Query, react-router,
react-pdf, react-force-graph. The UI language is **Slovak**; navigation is defined in `src/lib/nav.ts`.

```bash
pnpm install
pnpm dev        # http://localhost:5173, proxies /api and /mcp to the backend
pnpm build      # tsc -b && vite build
pnpm lint       # oxlint
```

The dev server proxies to `http://localhost:8000`; set `RAG_API` to point it at another backend
(Docker, a second instance). The backend itself starts with `make dev` from the repo root.

```
src/pages/          one file per route (chat, documents, graph, traces, tasks-board, attendance, ...)
src/components/     shared components; wf/ = company-module and worker-phone UI
src/components/ui/  shadcn/ui primitives (generated, see components.json)
src/hooks/          media-query and mobile helpers
src/lib/            API client (api.ts), navigation (nav.ts), worker/company helpers
```
