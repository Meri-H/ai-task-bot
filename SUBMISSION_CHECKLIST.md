# Assessment coverage and final checks

## Requirement mapping

| Assessment requirement | Implementation |
| --- | --- |
| Telegram Bot API, text input | aiogram polling, text handler, author storage, task acknowledgement |
| Voice input using Whisper or similar | Local faster-whisper by default; optional OpenAI provider |
| PostgreSQL tasks and users | SQLAlchemy models and Alembic migrations |
| Redis broker/queue for transcription and notifications | Separate arq queues and workers, retries, queued author notifications |
| Responsive dashboard using a modern JS framework | React, TypeScript, Kanban columns, mobile layout |
| Display all tasks | Cursor-paginated loading across the complete task list |
| Real-time updates | Redis pub/sub, WebSocket delivery, reconnect/resync handling |
| Pending / In Progress / Completed toggles | Status buttons and drag-and-drop with persistence |
| Dockerize frontend, API, database, Redis, and worker | Main Docker Compose file and service Dockerfiles |
| Voice resilience and quick feedback | Immediate acknowledgement, validation, retries, stored failures, duplicate-outcome replies |

Deletion with confirmation and task search are additional features.
The dashboard is a shared workspace; private user dashboards are not specified in the assessment.

## Live demonstration

Complete these checks on the machine used for the demonstration:

- [ ] Run the isolated Docker backend test command in the README and confirm there are no failures
      or unexpected skips. The editing environment's skipped integration tests are not a pass.
- [ ] Open the dashboard in two browser windows. Send a Telegram text message and confirm the task
      appears in both windows without refreshing, with the correct author.
- [ ] Send a short voice note after `faster-whisper model ready`. Confirm prompt acknowledgement,
      one saved task, and an edited success reply. Note the actual processing time on this CPU.
- [ ] Change the task status in one dashboard. Confirm the change appears in the other window and
      the Telegram author receives the status notification. Repeat while the voice worker is busy;
      the notification worker should deliver it independently.
- [ ] Cancel a task deletion and confirm the task stays. Then confirm deletion and check both windows.
- [ ] Record the IDs of remaining tasks. Run `docker compose down` without `-v`, then
      `docker compose up -d --no-build`. Confirm the same tasks, authors, and statuses are present.
- [ ] Stop the worker, send a voice note, and start the worker. Confirm the queued note is processed.
- [ ] Try a silent voice note. Confirm a helpful failure message and no empty task.
- [ ] Check the phone-width dashboard and search for a task by title or author.

## Before sending

- Keep `.env` private; provide `.env.example` for the reviewer.
- Include the README, source, tests, Dockerfiles, both Compose files, and migrations.
- State the shared-board assumption and that local transcription uses the host CPU without paid APIs.
- Report the checks actually run. Do not describe collected/skipped tests as passed or claim a fixed
  transcription speed independent of hardware and language.
