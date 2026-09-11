# Station Manager: um processo, uma porta (ZMQ REP 5580).
#
# Sem gcc nem libpq-dev: eram do psycopg2, e este serviço não conhece banco —
# de propósito, para que ele possa um dia rodar na máquina do rádio.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# git é obrigatório: o pip resolve `spacelab-tracking @ git+https://...`
# clonando. Sem ele o build falha com "Cannot find command 'git'".
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# A tag da biblioteca de tracking como ARG, e não só dentro do pyproject: assim
# `--build-arg TRACKING_REF=v0.2.0` invalida a layer do pip de forma explícita.
ARG TRACKING_REF=v0.1.0
ENV TRACKING_REF=${TRACKING_REF}

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir -e ".[zmq]"

# O compose sobrescreve o --bind e o --rotor; este default deixa a imagem
# executável sozinha (`docker run`) para diagnóstico.
CMD ["python", "-m", "mgm8.rotor_zmq.main", "--bind=tcp://0.0.0.0:5580", "--rotor=mock"]
