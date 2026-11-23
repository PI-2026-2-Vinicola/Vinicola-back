FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt "psycopg[binary]>=3.1" "pymysql>=1.1"

COPY app ./app
# Modelo publicado (ONNX + ficha): a imagem já sobe com o detector de cachos treinado.
COPY models ./models
RUN useradd --create-home --uid 1000 oasis && mkdir -p storage models && chown -R oasis:oasis /srv
USER oasis

EXPOSE 8000
# PORT: definida pela hospedagem (o Render usa 10000); 8000 quando não definida (docker compose).
# --proxy-headers: o IP e o esquema (https) vêm do proxy reverso, só se ele estiver em FORWARDED_ALLOW_IPS.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --no-server-header"]
