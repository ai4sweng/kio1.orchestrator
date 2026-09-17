# KIO1 orchestrator — interactive terminal app (LLM planner + KIO dispatcher).
# Build:  docker build -t kio1 .
#
# config.json is NOT baked into the image (it carries credentials); mount it
# at runtime. The orchestrator reaches host services via host.docker.internal,
# so override the two endpoints with env vars — one config.json serves both
# host and container:
#   docker run -it --add-host=host.docker.internal:host-gateway \
#     -v "$PWD/config.json:/app/config.json" \
#     -e KIO1_OLLAMA_ENDPOINT=http://host.docker.internal:11434 \
#     -e KIO1_KIO10_ADDRESS=http://host.docker.internal:8010 \
#     kio1
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# main.py is an interactive REPL; run the container with -it.
CMD ["python", "main.py"]
