FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY sentinel ./sentinel
COPY labs ./labs

RUN pip install --no-cache-dir .

EXPOSE 8000
ENV SENTINEL_LOG_JSON=1
CMD ["sentinel-api"]
