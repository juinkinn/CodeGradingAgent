# CodeGradingAgent

## Run locally

- Backend setup, SQLite migrations, RabbitMQ worker, and environment settings:
  see [backend/README.md](./backend/README.md).
- React instructor chat UI:
  from `frontend`, run `npm install` and `npm run dev`. The Vite app is served
  at `http://localhost:5173` and calls the API at `http://localhost:8000`.

## Run with Docker Compose

From the repository root, run `docker compose up --build`. This starts the
frontend at `http://localhost:8080`, the API at `http://localhost:8000`, the
submission-upload worker, and RabbitMQ. RabbitMQ's management UI is at
`http://localhost:15672`; the development credentials default to
`codegrading` / `codegrading-dev-password`. Change these credentials before
using this setup outside local development.

Optionally copy `backend/.env.example` to `backend/.env` to configure Gemini,
Langfuse, authentication, and telemetry. The app database and RabbitMQ data
persist in Docker volumes. Stop the services with `docker compose down`; to
also remove persisted data, run `docker compose down --volumes`.

Dockerfiles are provided at the repository root (backend image), `backend/`
(backend image with `backend` as the build context), and `frontend/`.

## Deploy to Render

Render does not run Docker Compose. Create a Blueprint from this repository;
Render reads [render.yaml](./render.yaml) to deploy the frontend and backend.
The backend service runs both the API and upload worker so they can share the
SQLite database in its temporary filesystem. The Blueprint uses Render's Free
web-service plan and does not require a persistent disk or payment method.
This is suitable only for demos: the backend can spin down after inactivity,
and the SQLite database is lost when it spins down, restarts, or redeploys.

Before deploying, create an external RabbitMQ broker (for example, CloudAMQP).
When Render prompts for environment values, provide its AMQP URL as
`RABBITMQ_URL`, your Gemini key as `GEMINI_API_KEY`, and your Langfuse keys as
`LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`. These Gemini and Langfuse keys
are required for the grading-agent features. The Blueprint sets the model,
Langfuse URL/label, and upload queue defaults, generates `AUTH_SECRET_KEY`,
and links the frontend URL to the backend's CORS and OAuth settings.
`GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` enable optional Google sign-in;
leave them blank if you do not use it. Configure the Google OAuth redirect URI
as `https://<backend-host>/auth/google/callback`. The OTLP endpoint and headers
are also optional; leave them blank if you do not export telemetry to Grafana.
Render's Free web service may take about a minute to wake after being idle.

## Run tests

From the repository root:

```powershell
Push-Location backend
python -m unittest discover -s test -v
Pop-Location

Push-Location frontend
npm run build
Pop-Location
```