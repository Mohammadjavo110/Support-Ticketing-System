# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1 — build the bundle (also proves the app compiles).
# `npm ci || npm install && npm run build` is left-associative in sh:
# ((npm ci) || (npm install)) && (npm run build) — the build always runs.
# ---------------------------------------------------------------------------
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
COPY . .
RUN npm ci || npm install && npm run build

# ---------------------------------------------------------------------------
# Stage 2 — run the Vite dev server.
# docker-compose overrides CMD with the same command, so the final image must
# be able to run `npm run dev`: ALL dependencies (incl. devDependencies such
# as vite and @vitejs/plugin-react) are installed here and sources are copied in.
# ---------------------------------------------------------------------------
FROM node:20-alpine AS dev
WORKDIR /app
# Never set NODE_ENV=production here: `npm run dev` needs devDependencies.
ENV NODE_ENV=development
COPY package*.json ./
RUN npm ci || npm install
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]
