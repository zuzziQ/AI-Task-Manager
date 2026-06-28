# Task Manager Bot — Python Application
FROM python:3.12-slim

# Set timezone
ENV TZ=Asia/Ho_Chi_Minh
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./src/
COPY service_account.json* ./

# Create non-root user for security
RUN useradd -m -u 1000 botuser && chown -R botuser:botuser /app
USER botuser

# Healthcheck — checks if bot process is running
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s \
    CMD pgrep -f "src.agent.bot" || exit 1

# Run bot
CMD ["python", "-m", "src.agent.bot"]
