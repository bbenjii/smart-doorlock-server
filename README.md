# Smart Lock Backend Server

Backend API and WebSocket server for the Smart Lock system.

This service handles:
- user authentication and token lifecycle
- device command dispatch (`LOCK`, `UNLOCK`, `GET_STATUS`)
- device and user WebSocket connections
- Firestore persistence (users, events, notifications, credentials, settings)
- media metadata persistence and local media file storage
- FCM push notification delivery

## Tech Stack

- Python 3.10+
- FastAPI + Uvicorn/Gunicorn
- Firebase Admin SDK
- Google Cloud Firestore
- Redis (latest camera frame cache)

## Project Structure

- `main.py`: FastAPI app, HTTP endpoints, router registration
- `routers/`: REST routes (`auth`, `notifications`, `settings`, `credentials`, `media`, `websockets`)
- `ws/`: device/client WebSocket session handling
- `services/`: business logic for auth, commands, events, notifications, settings, media
- `db/`: Firestore initialization and exported DB client
- `schemas/`: Pydantic request/response models

## Prerequisites

- Python 3.10+
- `pip`
- Firebase project with Firestore enabled
- Redis instance (optional but recommended)

## Configuration

The server currently expects these runtime values:

- `JWT_SECRET_KEY`: secret used to sign/verify JWTs
- `REDIS_URL`: Redis connection string (default: `redis://localhost:6379/0`)
- `FIREBASE_SERVICE_ACCOUNT_PATH` (optional for local dev): path to Firebase service-account JSON
- `FACE_INSIGHTFACE_MODEL` (optional, default `buffalo_sc`): insightface model pack name

Important:
- `db/database.py` initializes Firebase using this order:
- `FIREBASE_SERVICE_ACCOUNT_PATH`
- `GOOGLE_APPLICATION_CREDENTIALS`
- Application Default Credentials (recommended for Cloud Run)
- Avoid committing service-account JSON keys to git.

## Local Development Setup

1. Create a virtual environment:

```bash
python -m venv .venv
```

2. Activate the environment:

```bash
source .venv/bin/activate
```

3. Install dependencies:

```bash
python -m pip install -r requirements.txt
```

4. Start the API:

```bash
python main.py
```

Server defaults:
- Base URL: `http://localhost:8000`
- OpenAPI docs: `http://localhost:8000/docs`

## API Surface (High Level)

- Auth routes: `/auth/*`
- Settings routes: `/settings/*`
- Credential routes: `/credentials/*`
- Notification routes: `/notifications/*`
- Media routes: `/media/*`
- Device command route: `/send-command/{device_id}/{cmd}`
- Device status route: `/status/{device_id}`
- Event query routes: `/devices/{device_id}/events`, `/users/{user_id}/events`

## WebSocket Endpoints

- Device channel: `ws://<host>/ws/device`
- First message must be JSON with `{"type":"hello","deviceId":"..."}`
- Client channel: `ws://<host>/ws/client`
- First message must be JSON with `{"type":"subscribe","deviceId":"..."}`

## Docker

Build image:

```bash
docker build -t smart-doorlock-server .
```

Run container:

```bash
docker run --rm -p 8080:8080 \
  -e JWT_SECRET_KEY="replace-me" \
  -e REDIS_URL="redis://host.docker.internal:6379/0" \
  smart-doorlock-server
```

Container defaults:
- App listens on `PORT` env var, fallback `8080`
- Uses `gunicorn` + `uvicorn` worker
- Default worker count is `1` (`WEB_CONCURRENCY` can override)

## Firebase Deployment Direction

For this codebase (FastAPI + WebSockets), deploy on Cloud Run and optionally expose via Firebase Hosting rewrite.

## Security Notes

- Rotate and remove committed service-account JSON keys.
- Set a strong `JWT_SECRET_KEY` in production.
- Restrict CORS in `main.py` before production rollout.
