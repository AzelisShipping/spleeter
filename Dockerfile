FROM python:3.9-slim

# Install required dependencies
RUN apt-get update && apt-get install -y ffmpeg libsndfile1 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy Spleeter files
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir -e .
RUN pip install --no-cache-dir flask==2.0.1 werkzeug==2.0.1 gunicorn typer==0.3.2 click==7.1.2

# Install Google Cloud Storage (optional)
RUN pip install --no-cache-dir google-cloud-storage

# Set up the model directory
ENV MODEL_PATH=/app/models
RUN mkdir -p /app/models

# Set Google Cloud Storage configuration
# Set to "true" to enable GCS uploads
ENV USE_GCS=false
# Only needed if USE_GCS=true
ENV GCS_BUCKET_NAME=your_gcs_bucket_name

# Copy the web interface
COPY web /app/web

# Expose the port
EXPOSE 8080

# Run the web application
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "web.app:app"] 