FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 KIO_AGENT_ID=KIO3
COPY server.py ./server.py
USER 65534:65534
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=3s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)" || exit 1

CMD ["python", "server.py"]