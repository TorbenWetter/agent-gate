FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir .

COPY config.example.yaml permissions.example.yaml ./

VOLUME ["/app/data"]

EXPOSE 8443

CMD ["agentpass"]
