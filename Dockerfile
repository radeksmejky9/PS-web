FROM python:3.13

COPY . .

RUN mkdir -p /app/uploads
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 5000

CMD ["python", "app/app.py"]
