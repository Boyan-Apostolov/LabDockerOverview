FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agent.py .

# requires -v /var/run/docker.sock:/var/run/docker.sock at `docker run` time
CMD ["python", "agent.py"]
