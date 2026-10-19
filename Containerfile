FROM docker.io/library/python:3.11-slim-bookworm@sha256:0a310eeecf4e1f5a0743f9a6520c90c88d089c903ca5fd283f501e3a805f5f89
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    IONPATH_PUBLIC=1 STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential swig ca-certificates libstdc++6 libgomp1 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements.lock ./
RUN pip install -r requirements.lock
COPY scripts/install_itu_model.py scripts/itu_v14_3_manifest.json ./scripts/
RUN python scripts/install_itu_model.py
COPY *.py ./
COPY assets/ne_110m_land.geojson ./assets/ne_110m_land.geojson
RUN useradd --uid 10001 --create-home ionpath && chown -R ionpath:ionpath /app
USER ionpath
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=4)"
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
