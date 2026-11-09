# GameVault web app

The React single-page app that the GameVault server serves at `/`. React 19,
TypeScript, Vite, Tailwind CSS v4, Zustand for the few bits of shared state,
and react-virtuoso for the long screenshot grids.

## Working on it

Node 22 or newer.

```bash
npm ci
npm run dev        # http://localhost:5173, proxies /api and /share to a server on :8080
npm run lint
npm run build      # type-checks, then writes dist/ (what the Docker image ships)
npm run test:e2e   # Playwright; starts its own dev server
```

`npm run dev` needs a GameVault server on port 8080 for data: from the repository
root, `uvicorn backend.main:app --port 8080`.

## Layout

- `src/pages/` — one component per route (`App.tsx` has the route table);
  `pages/gallery/` is the public, signed-out gallery
- `src/components/` — shared pieces, grouped by feature
- `src/api/` — one module per server area; `client.ts` adds the sign-in token
- `src/stores/` — sign-in state and view preferences
- `e2e/` — Playwright tests

The version shown in the sidebar comes from `package.json`, which is bumped
together with `backend/version.py`. The rest of the project is documented in
[`docs/`](../docs/README.md).
