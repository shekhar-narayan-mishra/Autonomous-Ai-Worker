# syntax=docker/dockerfile:1
FROM python:3.11-slim

# Install system dependencies needed for Playwright Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    procps \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency definition and install Python packages
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browser and system OS libraries
RUN playwright install --with-deps chromium

# Copy application source code
COPY . ./

# Seed mock databases during build or startup
RUN python mock_env/seed.py

# Ensure start script is executable
RUN chmod +x entrypoint.sh

# Expose server port
EXPOSE 8000

ENV PORT=8000
ENV PYTHONUNBUFFERED=1

CMD ["./entrypoint.sh"]
