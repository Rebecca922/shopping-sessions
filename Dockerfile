FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends g++ \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --uid 1000 --no-create-home --home-dir /scratch app
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY src/ /app/src/
COPY scripts/build_native.py scripts/smoke_native.py /app/scripts/
RUN python /app/scripts/build_native.py && python /app/scripts/smoke_native.py
USER 1000:1000
CMD ["/app/core-native"]
