FROM python:3.12-slim

WORKDIR /app

# The base's dependencies, then every overlay's: an overlay that needs a
# package adds requirements.d/<overlay>.txt rather than editing the shared
# file (ADR-026). The glob matches nothing when there are none.
COPY requirements.txt .
COPY requirements.d/ ./requirements.d/
RUN pip install --no-cache-dir -r requirements.txt && for f in requirements.d/*.txt; do if [ -e "$f" ]; then pip install --no-cache-dir -r "$f"; fi; done

COPY . .

# The host injects PORT; the default matches local development.
ENV PORT=8000
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT}
