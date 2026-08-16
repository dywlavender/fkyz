# Risk Review Web Frontend

This directory contains the React + Vite + Ant Design frontend source for the risk review workbench.

## Offline Runtime

Offline runtime does not require `node_modules`.

The Python backend serves the built frontend from:

```text
risk_review_web/static/dist/
```

Start the backend and open:

```text
http://127.0.0.1:8765/
```

The React workbench is the only frontend entry point and is served at `/`. React deep routes are served from the same built entry point; deleted old pages and assets return `404`.

## Scheduled Tasks

The React scheduled-task page is backed by persistent JSON data in `risk_review_web/data/`. It supports import preview, CRUD, enable/disable, manual execution, automatic scheduling, run history, and cancellation of queued factors. Scheduled factor work always enters the backend's global task pool; the task concurrency value is only a per-run upper bound.

Supported schedules are once, daily, weekly, monthly, and standard five-field cron expressions. The scheduler poll interval can be changed with `RISK_REVIEW_SCHEDULER_POLL` (seconds, default `15`).

## Rebuild

```bash
cd risk_review_web/web
npm ci
npm run typecheck
npm run build
```

The build output is written to `risk_review_web/static/dist/`.

## Rebuild Offline

Prepare `offline_deps/npm-cache.tgz` in an online environment, then run in the offline environment:

```bash
bash offline_deps/install_frontend_offline.sh
bash offline_deps/build_frontend_offline.sh
```

Keep `package-lock.json`; it is required to reproduce the dependency graph. Do not commit `risk_review_web/web/node_modules/`.
