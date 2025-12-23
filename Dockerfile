FROM python:3.12

WORKDIR /app
COPY . /app

RUN apt-get -qq update && apt-get -qq install -y npm \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*
RUN python -m venv --copies /opt/venv

ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir frida-tools

EXPOSE 8000

ENV PORT=8000

CMD ["./start"]
