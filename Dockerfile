# Immagine ufficiale Microsoft Playwright con Chromium e dipendenze di sistema già preinstallate
FROM mcr.microsoft.com/playwright/python:v1.48.0-noble

WORKDIR /app

# Installa le dipendenze Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia il codice dell'agente e i dati configurati
COPY . .

# Crea la cartella per i dati, il DB SQLite e i cookie persistenti del browser
RUN mkdir -p /app/data/browser_state

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

