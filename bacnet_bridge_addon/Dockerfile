FROM python:3.12-slim

ARG BUILD_VERSION=0.3.8
LABEL \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="app" \
  io.hass.arch="aarch64|amd64"

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends iproute2 \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY bacnet_bridge /app/bacnet_bridge
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
