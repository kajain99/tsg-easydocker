FROM ubuntu:22.04

WORKDIR /app

ENV EASYDOCKER_HOST=0.0.0.0
ENV EASYDOCKER_USERNAME=admin

# Basic deps and Docker repository
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip \
    ca-certificates curl gnupg lsb-release \
    && mkdir -p /etc/apt/keyrings \
    && rm -f /etc/apt/keyrings/docker.gpg \
    && curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
    gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
    https://download.docker.com/linux/ubuntu \
    jammy stable" \
    > /etc/apt/sources.list.d/docker.list \
    && rm -rf /var/lib/apt/lists/*

# Docker CLI and Compose v2 plugin
RUN apt-get update && apt-get install -y --no-install-recommends \
    docker-ce-cli docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# App
COPY app /app

EXPOSE 5000

CMD ["python3", "app.py"]
