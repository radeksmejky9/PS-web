FROM python:3.10-slim

RUN apt-get update && apt-get install -y \
    blender \
    libsm6 \
    libxrender1 \
    libxext6 \
    && apt-get clean

COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/uploads && chmod 777 /app/uploads

EXPOSE 5000

CMD ["python", "/app/app.py"]
