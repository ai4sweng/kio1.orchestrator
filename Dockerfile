# KIO1 orchestrator — interactive terminal app (LLM planner + KIO dispatcher).
# Build:  docker build -t kio1 .
# Run:    docker run -it --add-host=host.docker.internal:host-gateway kio1
#
# The orchestrator reaches services on the host (Ollama, the KIO10 service)
# via host.docker.internal, not localhost. Either point config.json's
# provider endpoint and dispatch agent address at host.docker.internal, or
# mount a container config:
#   docker run -it --add-host=host.docker.internal:host-gateway \
#     -v "$PWD/config.docker.json:/app/config.json" kio1
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# main.py is an interactive REPL; run the container with -it.
CMD ["python", "main.py"]
