# Лёгкий образ для Yandex Serverless Containers: без LibreOffice и прочего тяжёлого — быстрее просыпается.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt && python -m compileall -q /usr/local/lib/python3.12/site-packages
COPY app ./app
COPY static ./static
RUN python -m compileall -q app

# Yandex Serverless Containers передаёт порт в переменной PORT.
ENV PORT=8080
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --no-access-log"]
