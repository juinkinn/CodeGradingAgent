# CodeGradingAgent

## Run locally

- Backend setup, SQLite migrations, RabbitMQ worker, and environment settings:
  see [backend/README.md](./backend/README.md).
- React instructor chat UI:
  from `frontend`, run `npm install` and `npm run dev`. The Vite app is served
  at `http://localhost:5173` and calls the API at `http://localhost:8000`.