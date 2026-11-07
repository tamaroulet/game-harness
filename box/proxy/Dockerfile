# 実装役の箱の中継（v3 §8 の E4）。拒みが既定で、allow に書いた宛先だけを通す。
FROM debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc

RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends tinyproxy-bin >/dev/null \
 && rm -rf /var/lib/apt/lists/*

COPY tinyproxy.conf /etc/tinyproxy.conf
COPY allow /etc/allow

USER nobody
CMD ["tinyproxy", "-d", "-c", "/etc/tinyproxy.conf"]
