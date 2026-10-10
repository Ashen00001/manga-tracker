FROM python:3.13-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    DATA_DIR=/data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY tracker ./tracker
COPY static ./static

EXPOSE 8787
VOLUME /data
CMD ["python", "-m", "tracker", "serve", "--host", "0.0.0.0", "--port", "8787"]
