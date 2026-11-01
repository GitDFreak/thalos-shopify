FROM python:3.15-slim@sha256:94d3426f1def7db4a106a232ac121bfd975393e5dfd40a619b955fae45fe7913

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

COPY . /app/thalos/

RUN useradd --create-home --uid 1000 thalos \
 && chown -R thalos:thalos /app
USER thalos

ENV PYTHONPATH=/app
ENTRYPOINT ["python", "-m", "thalos"]
