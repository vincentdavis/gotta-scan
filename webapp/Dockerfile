# GOTTA.SCAN web app for Railway.
# python:3.14 bookworm (glibc 2.36) satisfies the pymeshlab manylinux_2_35 wheel.
FROM python:3.14-slim-bookworm

# System libraries pymeshlab needs at runtime (it is Qt/OpenGL-based). Run Qt
# headless via QT_QPA_PLATFORM=offscreen so no display is required.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

ENV QT_QPA_PLATFORM=offscreen \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    GOTTASCAN_DEBUG=0

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

# Install dependencies first (cached unless the lockfile changes).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev

# Bake static assets (admin CSS etc.) into the image.
RUN uv run python manage.py collectstatic --noinput

# migrate runs at start against the mounted volume's SQLite DB; then serve.
CMD uv run python manage.py migrate --noinput && \
    uv run gunicorn config.wsgi --bind 0.0.0.0:${PORT:-8000} --workers 2 --timeout 600
