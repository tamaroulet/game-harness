# 許可リストのプロキシ（実験と計画 §6-3）。filter は config/scb_run.json の network.allow から harness/scb_run.py が作る
FROM ubuntu:22.04
RUN apt-get update && apt-get install -y --no-install-recommends tinyproxy && rm -rf /var/lib/apt/lists/*
COPY tinyproxy.conf /etc/tinyproxy/tinyproxy.conf
COPY filter /etc/tinyproxy/filter
EXPOSE 8888
CMD ["tinyproxy", "-d", "-c", "/etc/tinyproxy/tinyproxy.conf"]
