FROM node:20-slim

WORKDIR /app/

COPY package.json package-lock.json /app/
RUN npm ci

COPY frontend /app/frontend
COPY .parcelrc tsconfig.json /app/
RUN npm run lint && npm run build


FROM ghcr.io/jclgoodwin/bustimes.org/bustimes-base:3.14

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# --- FIX START: Install build tools needed for the patch ---
# We need git to clone the source and build-essential to compile it
RUN apt-get update && apt-get install -y \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*
# --- FIX END ---

WORKDIR /app/

COPY uv.lock pyproject.toml /app/

# --- FIX START: Manual Pyrobuf Patch ---
# 1. Create the virtual environment manually
RUN uv venv
ENV VIRTUAL_ENV=/app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# 2. Install build dependencies, then Clone, Patch, and Install pyrobuf
RUN uv pip install cython setuptools wheel jinja2 && \
    git clone https://github.com/appnexus/pyrobuf.git /tmp/pyrobuf && \
    # Inject 'self.dry_run = False' to fix the Python 3.14 compatibility error
    sed -i "s/self.include_dirs = None/self.include_dirs = None; self.dry_run = False/" /tmp/pyrobuf/setup.py && \
    # Install the patched version into the venv
    uv pip install /tmp/pyrobuf && \
    rm -rf /tmp/pyrobuf

# 3. Install remaining dependencies from lockfile
# We use 'uv export' with --no-hashes to prevent uv from trying to reinstall 
# the broken pyrobuf from PyPI due to hash mismatches.
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