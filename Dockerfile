FROM python:3.12-slim

# --- Dependências de sistema ---
# nodejs: necessário para yt-dlp processar JS do YouTube
# Sem nodejs, yt-dlp usa fallback android_vr que tem geo-bloqueio diferente do web player
RUN apt-get update && apt-get install -y \
    ffmpeg \
    fonts-dejavu-core \
    curl \
    nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# --- Dependências Python ---
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /data/m3us /data/epgs /data/logs

VOLUME ["/data"]
EXPOSE 8888

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8888/')"

CMD ["python3", "-m", "uvicorn", "web.main:app", "--host", "0.0.0.0", "--port", "8888"]
