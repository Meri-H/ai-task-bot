# AI Task Bot

A shared task board with a Telegram text/voice interface, a React dashboard, PostgreSQL storage,
and a Redis queue. Voice transcription runs locally on the worker's CPU by default, without paid
API calls. Tasks appear live on the dashboard and can be moved between Pending, In progress,
and Completed, searched, or deleted with confirmation.

## Architecture

| Component | Responsibility |
| --- | --- |
| Telegram bot (aiogram) | Validate messages, acknowledge voice notes, save text tasks, register voice jobs |
| PostgreSQL | Persist users, tasks, and voice-job outcomes |
| Redis / arq | Queue transcription and status notifications; distribute live events |
| Voice worker | Download Telegram audio, run Whisper, save tasks, resolve acknowledgements |
| Notification worker | Consume its own Redis queue and deliver Telegram status messages without waiting for voice jobs |
| FastAPI | Authenticated task API, WebSocket events, health checks, migrations at startup |
| React / TypeScript | Responsive Kanban board, search, drag-and-drop, status controls, confirmed deletion |
| Docker Compose | Start the frontend, API, bot, voice/notification workers, database, and Redis together |

A bot message becomes a database task. Redis pub/sub distributes its event to API instances,
which forward it to dashboards over WebSockets. Voice work goes through the queue before task
creation, so transcription never blocks the Telegram polling loop.

## Start on Windows

Install and open Docker Desktop using Linux containers. Extract the project and open PowerShell
in the folder containing `docker-compose.yml`.

```powershell
Copy-Item .env.example .env
notepad .env
```

Create a bot through Telegram's official `@BotFather` and set `TELEGRAM_BOT_TOKEN` in `.env`.
Keep these settings for local transcription without API charges:

```dotenv
TRANSCRIPTION_PROVIDER=local
OPENAI_API_KEY=
TRANSCRIPTION_FALLBACK_TO_LOCAL=false
WHISPER_LOCAL_MODEL=base
WORKER_CONCURRENCY=1
```

Build and start:

```powershell
docker compose build --build-arg INSTALL_LOCAL_WHISPER=true
docker compose up -d --no-build --remove-orphans
docker compose logs -f worker
```

Wait for `faster-whisper model ready`, then open <http://localhost:3000>. Send the bot `/start`, a
text message, and a short voice note. The first worker startup downloads the model; later starts
reuse the `whisper-models` volume. Telegram still needs internet. Ctrl+C stops following logs,
without stopping the services.

Only the Telegram token is needed for the local mode. OpenAI remains an optional provider selected
explicitly with `TRANSCRIPTION_PROVIDER=openai` and an API key; that provider has separate API charges.
The pinned faster-whisper/PyAV versions avoid the incompatible `metadata_errors` decoder call.

## Update an existing installation

Replace the project files in the existing folder and retain your current `.env`. Then run:

```powershell
docker compose build --build-arg INSTALL_LOCAL_WHISPER=true
docker compose up -d --no-build --remove-orphans
```

The changes do not require a new database migration. Existing tasks remain in the database volume.
The orphan removal stops services from older project versions that are absent from this Compose file.

## Daily use

- The dashboard is one shared workspace. Cards show their Telegram author or Dashboard source.
- All tasks load through cursor pagination, rather than stopping after the first 500. Each desktop
  column scrolls independently; mobile columns stack vertically.
- New tasks, status changes, and deletions propagate live. Changes received during loading are
  preserved, and reconnecting dashboards refresh from the API.
- Change status using the buttons or drag a card. Telegram authors receive a queued notification.
- Delete using the trash icon, then confirm. Cancelling keeps the task; a failed request leaves it
  available for retry. Voice-job history remains after a task is deleted.
- `/tasks` lists the Telegram sender's open tasks; `/help` explains supported inputs.
- A duplicate voice update does not queue another transcription. Its new acknowledgement is resolved
  with the recorded status, including an already-created task or a previous failure.

```powershell
docker compose ps
docker compose logs --tail=100 bot worker api
docker compose down
docker compose up -d --no-build
```

`down` retains named data volumes. Do not add `-v` when you want to keep your saved tasks.

## Local transcription speed

The worker preloads the model, uses CPU int8 inference, beam size 1, and a silence filter. Processing
speed still depends on CPU resources and recording length. Short voice notes are best for quick tasks.
For a faster, generally less accurate model, set `WHISPER_LOCAL_MODEL=tiny` in your existing `.env`,
then recreate the worker:

```powershell
docker compose up -d --no-build --force-recreate worker
```

The smaller model downloads on its first start. Keep `base` when recognition accuracy matters more.
No model change is necessary for the functional fixes in this update.

## API

Interactive API documentation: <http://localhost:3000/api/docs>.

| Endpoint | Behavior |
| --- | --- |
| `GET /api/tasks` | List tasks; supports status, limit, offset, or a timestamp/ID cursor |
| `GET /api/tasks/{id}` | Read one task |
| `POST /api/tasks` | Create a dashboard task |
| `PATCH /api/tasks/{id}` | Change status |
| `DELETE /api/tasks/{id}` | Permanently remove a task; return 204, or 404 when missing |
| `GET /api/health` | Check database and Redis connectivity |
| `/api/ws` | Stream task events and answer heartbeat messages |

For a cursor page, pass both `before_created_at` and `before_id` from the previous page's last task.
The timestamp must include a timezone; cursor pagination cannot be combined with a nonzero offset.
Ordering uses creation timestamp and ID, so insertions or deletions do not shift page boundaries.
`total` counts all matching tasks, rather than only tasks older than the cursor.

Events are `task.created`, `task.updated`, and `task.deleted`; the deletion event contains `task_id`.
`resync` tells dashboards to reload. Optional `DASHBOARD_TOKEN` protects task routes and WebSockets.
The health endpoint stays open. `TELEGRAM_ALLOWED_USER_IDS` can restrict who may use the bot.

## Tests

The frontend Docker build runs its tests and TypeScript/Vite build. To run the backend unit,
integration, and process-level end-to-end suite with separate temporary database and Redis services:

```powershell
docker compose -p taskbot-check -f docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from tests
```

The test setup does not load your `.env`, use the application database, send real Telegram messages,
call paid APIs, or download Whisper model weights. It substitutes local fake Telegram/OpenAI servers
while exercising the real API, bot, worker, PostgreSQL, Redis queues, and WebSocket processes. Missing
test services cause a failure in this Docker runner instead of silently skipping integration checks.

Remove the test containers afterward:

```powershell
docker compose -p taskbot-check -f docker-compose.test.yml down
```

For local development, install backend extras with `pip install -e ".[dev,local]"`, then run `pytest`.
Set `TEST_DATABASE_URL` and `TEST_REDIS_URL` to dedicated test services. Database-backed tests reset
the test schema. Without those services, unit tests run and integration tests report explicit skips.
For the frontend, use `npm ci`, `npm test`, and `npm run build` from the frontend folder.

## Validation and scope

- 23 frontend tests and the TypeScript/Vite production build pass.
- 47 backend unit tests, a real-Redis worker isolation check, and Python lint pass in the editing environment.
- A browser check loads 620 tasks, preserves live creation/status/deletion events during paging,
  searches the oldest task, deletes it, and checks desktop column scrolling and mobile overflow.
  This browser check uses mocked API/WebSocket responses.
- 86 PostgreSQL/integration/end-to-end tests are collected but skipped in the editing environment,
  which has no running PostgreSQL or Docker daemon. Run the Docker test command above for those
  checks; they are not claimed as passed.
- Local WAV and OGG/Opus decoding has been checked with the pinned libraries. Actual speech
  recognition speed and accuracy must be checked on the deployment machine.

See `SUBMISSION_CHECKLIST.md` for the assessment mapping and live demonstration steps.

The board is intentionally shared: the assessment does not require private per-user dashboards.
A single dashboard token is access control for the shared workspace, rather than user accounts.
PostgreSQL is the source of truth; Redis events are best-effort and dashboards resync on reconnect.
Queue retries cover transient provider/Telegram failures; known billing errors are permanent.
Creation is idempotent per Telegram message. Failed voice jobs remain recorded for inspection;
new recordings are separate jobs.

Further production work could include per-user accounts, durable event/outbox delivery, metrics,
rate limiting, language evaluation, and recovery of jobs interrupted between database commit and
queue publication. This assessment implementation does not claim those features.

## Independent status notifications

Voice transcription and status delivery have separate Redis queues and separate worker processes.
The `notifications` Compose service consumes `taskbot:notifications` without importing a model into
memory or waiting for Whisper warmup. The voice worker consumes `taskbot:jobs`; `WORKER_CONCURRENCY=1`
can therefore remain in place for CPU voice processing. `NOTIFICATION_WORKER_CONCURRENCY` defaults to 4.
Notification polling runs every 0.1 seconds, independently of voice-job duration. Telegram network
latency and Telegram rate limits can still affect delivery time; this change removes the shared-worker wait.

After copying the updated files into an existing project folder and keeping `.env`, run:

```powershell
docker compose up -d --build --remove-orphans
docker compose logs --tail=30 notifications
```

New status changes use the dedicated queue. Existing notifications already queued by an older
version are retained and drained by the voice worker for upgrade compatibility. The new notification
worker does not need local Whisper dependencies. No database migration is required.

A regression check runs two real arq workers against Redis, deliberately blocks the voice worker,
and confirms a newly queued notification finishes before that voice worker is released. An additional
process-level test verifies status delivery with the voice worker stopped; this PostgreSQL-backed
check is included in the isolated Docker test suite.

## End-to-end test startup checks

The test harness waits for each arq worker's Redis heartbeat after its startup hook completes,
and for the fake Telegram server to observe the bot actually polling. It clears an old heartbeat
before launching a replacement worker. Readiness is independent of log wording and uses one bounded
90-second startup budget for the stack, configurable with `E2E_STARTUP_TIMEOUT_SECONDS` in the test
Compose file. A child process that exits early fails immediately with its PID, exit code, and logs.
Ordinary action waits remain short; the extra budget is for cold process startup under Docker Desktop.

The five startup-harness regression tests pass. The startup probe can verify real worker processes,
Redis heartbeats, and fake-Telegram bot polling without PostgreSQL; the complete Docker end-to-end
suite still needs to be rerun on the deployment machine.

A real-process cold-start check also deliberately delays the voice worker by 16 seconds before
importing its CLI. The heartbeat check accepts the worker once ready (about 25 seconds in the
editing environment), rather than failing at the old 15-second limit. This timing is a controlled
startup check, not a production transcription benchmark.
