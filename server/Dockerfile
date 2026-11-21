FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/ender
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 ender && useradd --uid 10001 --gid ender --no-create-home ender \
    && install -d -o ender -g ender /data
COPY requirements.in requirements-vision.txt ./
RUN pip install --no-cache-dir -r requirements.in -r requirements-vision.txt
COPY . .
RUN python provision_model.py
USER ender
EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
