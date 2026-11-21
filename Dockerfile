FROM gcc:14-bookworm AS build
ARG TARGETARCH
WORKDIR /build
COPY src/main.cpp .
RUN if [ "$TARGETARCH" = "amd64" ]; then \
      g++ -O3 -DNDEBUG -std=c++20 -pthread \
          -static-libstdc++ -static-libgcc \
          -march=x86-64-v3 main.cpp -o /build/core; \
    else \
      g++ -O3 -DNDEBUG -std=c++20 -pthread \
          -static-libstdc++ -static-libgcc \
          main.cpp -o /build/core; \
    fi

FROM python:3.12-slim-bookworm
RUN useradd --uid 1000 --no-create-home --home-dir /scratch app
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=build /build/core /app/core
COPY run.py /app/run.py
RUN python -c "import subprocess; p = subprocess.run(['/app/core'], capture_output=True, text=True); assert p.returncode == 1 and 'usage: core ' in p.stderr, p.stderr"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 1000:1000
CMD ["python", "/app/run.py"]
