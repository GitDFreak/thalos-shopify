FROM python:3.14-slim@sha256:bc389f7dfcb21413e72a28f491985326994795e34d2b86c8ae2f417b4e7818aa

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
