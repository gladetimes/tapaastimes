FROM node:20-slim

WORKDIR /app/

COPY package.json package-lock.json /app/
RUN npm ci

COPY frontend /app/frontend
COPY .parcelrc tsconfig.json /app/
RUN npm run lint && npm run build


FROM ghcr.io/jclgoodwin/bustimes.org/bustimes-base:3.14

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 1. Install system build tools (Debian-based)
RUN apt-get update && apt-get install -y \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/

COPY uv.lock pyproject.toml /app/

# --- FIX START: ROBUST PYROBUF PATCH ---
RUN uv venv
ENV VIRTUAL_ENV=/app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# 2. Install older setuptools (safer for legacy builds) and build tools
RUN uv pip install "setuptools<70" wheel cython jinja2

# 3. Clone and Patch pyrobuf
# We target 'Distribution.__init__' which is standard, ensuring the patch applies.
RUN git clone https://github.com/appnexus/pyrobuf.git /tmp/pyrobuf && \
    sed -i "s/Distribution.__init__(self, attrs)/Distribution.__init__(self, attrs); self.dry_run = False/" /tmp/pyrobuf/setup.py && \
    uv pip install /tmp/pyrobuf && \
    rm -rf /tmp/pyrobuf

# 4. Install remaining dependencies
RUN uv export --frozen --no-hashes --format=requirements-txt > requirements.txt && \
    uv pip install -r requirements.txt
# --- FIX END ---

COPY --from=0 /app/node_modules/htmx.org/dist /app/node_modules/htmx.org/dist
COPY --from=0 /app/node_modules/reqwest/reqwest.min.js /app/node_modules/reqwest/
COPY --from=0 /app/busstops/static /app/busstops/static
COPY . /app/

ENV PORT=8000 STATIC_ROOT=/staticfiles
RUN ./manage.py collectstatic --noinput

EXPOSE 8000
CMD ["gunicorn", "buses.wsgi"]