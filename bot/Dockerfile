FROM python:3.12-slim

WORKDIR /app

# Install Python dependencies before copying application code for better layer caching.
COPY bot/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Create the default data directory. On Railway, attach a Volume at /app/data
# so accounts survive deployments and container restarts.
RUN mkdir -p /app/data

COPY bot/main.py ./

CMD ["python", "main.py"]
