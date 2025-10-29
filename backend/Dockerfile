# Python base
FROM python:3.11-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

# upgrade pip en tools
RUN pip install --upgrade pip setuptools wheel


# daarna de rest
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app



EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
