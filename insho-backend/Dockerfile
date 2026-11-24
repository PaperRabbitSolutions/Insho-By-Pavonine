
# Use the same Python version as the existing local environment
FROM python:3.9-slim

# Prevent Python from writing unnecessary bytecode
ENV PYTHONDONTWRITEBYTECODE=1

# Display Python logs immediately
ENV PYTHONUNBUFFERED=1

# Set container working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency list
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy existing backend project
COPY . .

# Application port
EXPOSE 8000

# Start FastAPI using Uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
