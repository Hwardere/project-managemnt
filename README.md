# Property Manager

A simple web app for tracking residential/rental properties. Pick a property's
address and everything about it — utility accounts, insurance, landscaping,
occupancy, documents, and to-do tasks — appears on one screen. Built to replace
the scattered spreadsheets, folders, and sticky notes property management usually
lives in.

## Roles

Login is by **password only** (no usernames); your role is determined by which
password you enter:

- **Admin** — full control: add/edit/delete properties, manage everything.
- **Worker** — view everything, edit fields, upload documents, complete tasks;
  **cannot** add or delete properties or files.

## Features

- **Per-property records**, organized into tabs:
  - **Overview** — insurance (policy #, expiration, renewal, premium),
    landscaping schedule, utility accounts, and color-coded due-date alerts.
  - **Occupancy** — occupied/vacant; if occupied: tenant, rent, lease dates,
    and the lease document.
  - **Documents** — one file per topic (Deed, Insurance policy, Tax records,
    Survey, Mortgage, Inspection, Warranty); re-uploading replaces the file.
  - **Tasks** — to-dos for that specific property.
- **To-Do & Calendar** — a shared, cross-property month calendar (tasks shown on
  their due dates, color-coded for overdue/upcoming/done) plus a filterable
  to-do list everyone works from.
- **File uploads from any device**, stored centrally (max 25 MB per file).

## Tech

Deliberately dependency-free:

- **Backend:** `app.py` — Python standard library only (no frameworks). Serves
  the page and a small REST API; stores data as JSON; saves uploads to disk.
- **Frontend:** `index.html` — plain HTML/CSS/JavaScript, no build step.
- **Data:** lives in `data/` — `db.json` (properties), `tasks.json` (tasks),
  `uploads/` (files). This folder is git-ignored and stays on the host.

## Run locally

```bash
python3 app.py
# then open http://localhost:8000
```

Default test passwords: `admin123` / `team123`.

## Configuration

Set via environment variables (recommended for any real deployment):

| Variable          | Default    | Purpose                  |
|-------------------|------------|--------------------------|
| `ADMIN_PASSWORD`  | `admin123` | Admin login password     |
| `WORKER_PASSWORD` | `team123`  | Worker login password    |
| `PORT`            | `8000`     | Port the server binds to |

```bash
ADMIN_PASSWORD='choose-a-strong-one' WORKER_PASSWORD='team-shared-pw' python3 app.py
```

## Deployment

The app runs anywhere Python 3 runs. To make it reachable by staff on their own
devices, deploy to a host (e.g. Render) with a persistent disk mounted at
`data/` so uploaded files and records survive restarts, and set the password
environment variables above.

## Status

Working locally. Recurring/automated maintenance schedules (auto-populating
bi-weekly/quarterly/annual checkups) are designed but not yet implemented.
