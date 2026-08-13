FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Keep the deployment entrypoint explicit so Railway cannot start the
# retired PostgreSQL-based bot_app.py process from an older image.
ENV PYTHONUNBUFFERED=1
CMD ["python", "/app/bot.py"]
