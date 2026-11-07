# 実装役・門・採点の箱（v3 §8 の E4）。版はすべて固定する。
# 土台の digest と部品の版と agy の SHA-512 は、harness/box.py の指紋に入る（変えたら像を造り直す）。
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

ARG AGY_URL
ARG AGY_SHA512

RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends git ca-certificates curl libgl1 libegl1 >/dev/null \
 && rm -rf /var/lib/apt/lists/*

# 鎖の課題が使う部品。Windows の基準の走りと同じ版
RUN pip install --no-cache-dir --quiet --root-user-action=ignore mujoco==3.15.0 numpy==2.5.3 glfw==2.10.2 PyYAML==6.0.3

# agy は root の持ち物の置き場に置く。実装役からは読みと実行だけ（自動更新は「書けない置き場」として飛ばされる）
RUN curl -fsSL -o /tmp/agy.tar.gz "$AGY_URL" \
 && echo "$AGY_SHA512  /tmp/agy.tar.gz" | sha512sum -c - \
 && tar -xzf /tmp/agy.tar.gz -C /usr/local/bin antigravity \
 && mv /usr/local/bin/antigravity /usr/local/bin/agy \
 && chmod 755 /usr/local/bin/agy \
 && rm /tmp/agy.tar.gz

RUN useradd -u 1000 -m bench && mkdir /work && chown bench /work

ENV AGY_CLI_DISABLE_AUTO_UPDATE=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1
USER bench
WORKDIR /work
