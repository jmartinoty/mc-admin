FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Le contenu de app/ est copié à la racine du WORKDIR : les imports sont absolus
# (config, domain, adapters, api) — cf. pytest.ini (pythonpath = app).
COPY app/ .

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
