# Use a lightweight official Python image
FROM python:3.11-slim

# Set system working directory inside the container
WORKDIR /app

# Install system dependencies required for compiling certain Python packages (like psycopg2)
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file first to leverage Docker's caching mechanism
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your pipeline code into the container
COPY . .

# Set environment variables to optimize Python execution inside containers
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Command to execute your pipeline when the container starts
CMD ["python", "scheduler.py"]