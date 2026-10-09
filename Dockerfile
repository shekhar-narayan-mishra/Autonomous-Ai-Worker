# syntax=docker/dockerfile:1
FROM mcr.microsoft.com/playwright/python:v1.42.0-jammy

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Ensure Chromium browser is ready
RUN playwright install chromium

# Copy application source code
COPY . ./

# Seed mock databases during build
RUN python mock_env/seed.py

# Ensure start script is executable
RUN chmod +x entrypoint.sh

# Expose server port
EXPOSE 8000

ENV PORT=8000
ENV PYTHONUNBUFFERED=1

CMD ["./entrypoint.sh"]
