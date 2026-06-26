FROM python:3.11-slim

WORKDIR /app

# Install system dependencies if any are needed (e.g. for pypdf)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Expose port (Railway dynamically sets the PORT environment variable, usually defaults to 8000 but we'll bind to $PORT)
ENV PORT=8000
EXPOSE $PORT

# Command to run the application (assuming app.main:app is your FastAPI instance)
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
