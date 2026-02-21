# personal-stylist-ai

Agentic personal stylist runtime with Google Drive photo ingestion, multi-step recommendation flow, and checkout draft generation.

## Services
- `api`: FastAPI routes for run lifecycle and Drive OAuth/folder management.
- `orchestrator`: polls DB, queues the next runnable step.
- `workers`: executes one step and writes artifacts.
- `postgres`, `redis`, `minio`: infrastructure dependencies.

## One-time setup
1. Create a Google Cloud OAuth app.
2. Enable Google Drive API.
3. Add redirect URI: `http://localhost:8000/api/drive/oauth/callback`.
4. Export environment variables (or put the same keys in `.env`):
```bash
export GOOGLE_CLIENT_ID='...'
export GOOGLE_CLIENT_SECRET='...'
export GOOGLE_OAUTH_REDIRECT_URI='http://localhost:8000/api/drive/oauth/callback'
export GOOGLE_DRIVE_SCOPE='https://www.googleapis.com/auth/drive.readonly'
export OPENAI_API_KEY='...'
export OPENAI_API_BASE='https://api.openai.com/v1'
export OPENAI_VISION_MODEL='gpt-4.1-mini'
export SERPAPI_ENDPOINT='https://serpapi.com/search.json'
export PRODUCT_DATA_MODE='auto'   # auto|mock|serpapi
export SERPAPI_API_KEY='...'      # required when PRODUCT_DATA_MODE is auto or serpapi
export ORCHESTRATOR_POLL_INTERVAL_SECONDS='1.0'  # optional
export ORCHESTRATOR_RUN_ONCE='0'                  # optional
```

## Start the stack
```bash
./infra/scripts/dev_up.sh
```

## Real testing quick path
1. Create `.env` and fill real credentials:
```bash
cp .env.example .env
```
2. Run guided real test (replace with your email):
```bash
./infra/scripts/real_test.sh you@example.com
```
Optional: preselect a folder in the same command:
```bash
./infra/scripts/real_test.sh you@example.com <folder_id>
```
Mock-only mode (skip SerpAPI key requirement):
```bash
# set PRODUCT_DATA_MODE=mock in .env, then run:
./infra/scripts/real_test.sh you@example.com
```
3. If Drive is not connected yet, the script prints an OAuth URL.
4. Open that URL in browser, grant access, then rerun the same command.
5. If no folder is selected, script prints folder ids and the select command.
6. Rerun after selecting folder; it will create a run, poll until done, and print:
- step statuses
- artifact kinds
- `STYLE_BRIEF` analysis method (`multimodal_llm` / `heuristic_fallback` / `none`)
- `DEALS` and `BRAND_SEARCH` data mode/provider (`serpapi` when live catalog data is pulled)

## Connect Google Drive
1. Request OAuth URL:
```bash
curl -X POST http://localhost:8000/api/drive/oauth/start \
  -H 'content-type: application/json' \
  -d '{"email":"you@example.com"}'
```
2. Open `auth_url` in browser, grant access.
3. Callback completes at `/api/drive/oauth/callback`.
4. List folders:
```bash
curl "http://localhost:8000/api/drive/folders?email=you@example.com"
```
5. Select one folder:
```bash
curl -X POST http://localhost:8000/api/drive/folder/select \
  -H 'content-type: application/json' \
  -d '{"email":"you@example.com","folder_id":"<folder_id>","folder_name":"<optional>"}'
```
6. Optional: preview selected folder photos:
```bash
curl "http://localhost:8000/api/drive/photos?email=you@example.com&limit=10"
```

## Run the product flow
Create run:
```bash
curl -X POST http://localhost:8000/api/runs \
  -H 'content-type: application/json' \
  -d '{"email":"you@example.com","trigger":"manual"}'
```

Fetch run status and artifacts:
```bash
curl http://localhost:8000/api/runs/<run_id>
```

Inspect `STYLE_BRIEF` output:
```bash
curl http://localhost:8000/api/runs/<run_id> | jq '.artifacts[] | select(.kind=="style_brief") | .inline_json'
```

Expected `style_brief.inline_json.analysis_method` values:
- `multimodal_llm`: Drive photos analyzed with multimodal model.
- `heuristic_fallback`: photo-based fallback when multimodal call fails.
- `none`: Drive not connected, no selected folder, or no photos.

Expected product data behavior:
- live provider path: SerpAPI Google Shopping (`gl=us`, `hl=en`) for `DEALS` and `BRAND_SEARCH`.
- with `SERPAPI_API_KEY` present and `PRODUCT_DATA_MODE=auto|serpapi`, `DEALS` and `BRAND_SEARCH` pull live shopping results.
- if provider errors or returns no results, flow automatically falls back to mock candidates and marks `data_mode=mock_fallback`.

## Stop
```bash
./infra/scripts/dev_down.sh
```
