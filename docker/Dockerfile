# One image, every service. They share a codebase and differ only by command, so building
# four near-identical images would just be four chances for them to drift apart.
#
# The webui stage below is build-only: it needs Node to produce static assets, but nothing
# in the final image runs Node -- only server/api/app.py serves the resulting
# src/webui/dist as static files (config.webui_dist). Every service still shares the one
# final image (ingest/worker/uploader never touch those files at runtime); the extra static
# assets cost nothing there and keep this genuinely one image, not two.
FROM node:22-slim AS webui-build
WORKDIR /webui
COPY src/webui/package.json src/webui/package-lock.json ./
RUN npm ci
COPY src/webui/ ./
# ARG, not ENV: Vite bakes `import.meta.env.VITE_*` into the built JS at compile time --
# never read again at container runtime, so a running container can't be "reconfigured"
# without a rebuild. VITE_DATA_SOURCE=api is fixed here on purpose: this is the deployed
# dashboard server, not the mock-mode prototype, so there is no reason it would ever want
# fixture data. VITE_VIEWER_ID is the MVP's trusted-header stand-in (F2 gate;
# src/webui/src/vite-env.d.ts has the full contract) -- a single-operator local/demo stack
# default, empty (no header, today's safe default -- every manager screen 401s until set)
# unless overridden via `docker compose build --build-arg VITE_VIEWER_ID=...` or
# STACK_VIEWER_ID (docker-compose.stack.yml). A real multi-user deployment replaces this
# entirely with an authenticating reverse proxy in front of the whole stack instead, which
# injects the header per browser session -- never one baked into a shared JS bundle for
# every visitor alike.
ARG VITE_DATA_SOURCE=api
ARG VITE_VIEWER_ID=
ENV VITE_DATA_SOURCE=${VITE_DATA_SOURCE} \
    VITE_VIEWER_ID=${VITE_VIEWER_ID}
RUN npm run build

FROM python:3.12-slim

WORKDIR /app

# Dependencies first, so a source edit does not re-resolve the whole tree on every build.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY config/ ./config/
# Overwrite the source tree's own src/webui/dist (gitignored, absent in a fresh checkout)
# with the real build from the stage above, at the same relative path config.webui_dist
# (default "src/webui/dist") already expects -- no config change needed to pick it up.
COPY --from=webui-build /webui/dist/ ./src/webui/dist/

# `pythonpath = . src tests` is pytest.ini's; a container has no pytest to read it.
ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1

# The staging root every config reaches through ${TALIUM_DATA_DIR}: the uploader writes it,
# the collector reads it, and msging's producer store lives in it. A named volume mounts here.
ENV TALIUM_DATA_DIR=/data
RUN mkdir -p /data

# No default command -- compose gives each service its own. An image that starts *something*
# when run bare is an image that starts the wrong thing eventually.
