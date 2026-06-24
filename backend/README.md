# ZO RFP FastAPI Backend

Python API for the RFP dashboard. Shares the same SQLite database and PDF storage as `rfp-dashboard/`.

## Setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Run

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --port 8001
```

- API docs: http://localhost:8001/docs
- Health: http://localhost:8001/api/v1/health

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/health` | Service health + DB path |
| GET | `/api/v1/rfps` | List all RFPs |
| GET | `/api/v1/rfps/dashboard` | Dashboard payload (rfps + stats) |
| GET | `/api/v1/rfps/{id}` | Single RFP |
| POST | `/api/v1/rfps` | Create manual RFP (JSON or multipart) |
| POST | `/api/v1/rfps/{id}/go` | Mark RFP as Go |
| GET | `/api/v1/rfps/{id}/pdf` | Stream RFP PDF |
| GET | `/api/v1/knowledge-base/status` | Supermemory + Drive status |
| GET | `/api/v1/knowledge-base/folders` | Folders in shared drive **RFPs** |
| POST | `/api/v1/knowledge-base/connect/google-drive` | Start Supermemory Drive OAuth |
| POST | `/api/v1/knowledge-base/sync/google-drive` | Trigger Supermemory sync |

## Knowledge base (Supermemory + Google Drive)

Set in `backend/.env`:

```
SUPERMEMORY_API_KEY=your-key
SUPERMEMORY_CONTAINER_TAG=zo-agency
GOOGLE_CLIENT_ID=....apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=...
GOOGLE_REFRESH_TOKEN=...
GOOGLE_DRIVE_SHARED_DRIVE_NAME=RFPs
APP_URL=http://localhost:3000
```

### Google OAuth setup (one time)

```bash
cd backend
source .venv/bin/activate
pip install google-auth-oauthlib
python scripts/google_oauth_setup.py
```

Sign in with the Google account that can access your Drive folders. Copy the printed `GOOGLE_REFRESH_TOKEN` into `backend/.env`.

All proposals, knowledge-base uploads, and RFP PDFs ingest into the **single** `SUPERMEMORY_CONTAINER_TAG` (default `zo-agency`).

## Bulk ingest from Google Drive folder

Script: `scripts/ingest_drive_folder_to_supermemory.py`

Reads every file in one Drive folder, categorizes by ZO filename prefix (`06_WON_`, `07_FIN_`, `03_CS_`, `11_REF_`, `zo_`, etc.), and uploads directly to Supermemory. **No local storage.**

```bash
cd backend
source .venv/bin/activate

python scripts/ingest_drive_folder_to_supermemory.py \
  --folder-name "6. RFP CLAUDE Specialis" \
  --dry-run

python scripts/ingest_drive_folder_to_supermemory.py \
  --folder-id "YOUR_FOLDER_ID"
```

Requires `SUPERMEMORY_API_KEY`, `SUPERMEMORY_CONTAINER_TAG`, and Google OAuth vars in `backend/.env`.


## JustWin Playwright

JustWin browser sync is **disabled in the frontend** for now. Re-enable via `JUSTWIN_SYNC_ENABLED` in `rfp-dashboard/src/lib/justwin-config.ts`.
