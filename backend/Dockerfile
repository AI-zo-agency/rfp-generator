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

EXPOSE 8000

# Command to run the application
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 8000"]
