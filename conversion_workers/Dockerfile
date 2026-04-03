FROM python:3.12-slim

WORKDIR /app

ENV PYTHONPATH=/app

RUN apt-get update && \
    apt-get install -y ghostscript && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY conversion_workers/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt


COPY conversion_workers/ ./conversion_workers
COPY common_logging/ ./common_logging
COPY shared_database/ ./shared_database

CMD ["python3", "-m", "conversion_workers.main"]