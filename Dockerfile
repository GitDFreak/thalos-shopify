FROM python:3.13-slim@sha256:d168b8d9eb761f4d3fe305ebd04aeb7e7f2de0297cec5fb2f8f6403244621664

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
