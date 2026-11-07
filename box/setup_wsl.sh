#!/bin/bash
# WSL2 の準備（v3 §8 の E4）。root で走らせる。何度走らせてもよい。python -m harness.box setup が標準入力で渡す。
# 引数：agy の版・URL・SHA-512（config/box.json）。
# - bench 利用者：agy のログインの持ち主。箱の中の利用者と同じ uid 1000。docker の組には入れない。
# - agy：bench がログインするための本物。root の持ち物の置き場に置き、自動更新は切る。
# - Docker Engine：箱の置き場。操作は root だけ。
# - /etc/wsl.conf：WSL2 から Windows のフォルダと実行ファイルを見えなくする（効くのは wsl --terminate の後）。
set -euo pipefail

AGY_VERSION=$1
AGY_URL=$2
AGY_SHA512=$3
TOOLS=/opt/tools

id bench >/dev/null 2>&1 || useradd -m -s /bin/bash -u 1000 bench
grep -q 'AGY_CLI_DISABLE_AUTO_UPDATE' /home/bench/.profile || echo 'export AGY_CLI_DISABLE_AUTO_UPDATE=1' >> /home/bench/.profile

if [ ! -x "$TOOLS/agy-$AGY_VERSION/agy" ]; then
  work=$(mktemp -d)
  trap 'rm -rf "$work"' EXIT
  curl -fsSL -o "$work/agy.tar.gz" "$AGY_URL"
  echo "$AGY_SHA512  $work/agy.tar.gz" | sha512sum -c - >/dev/null
  tar -xzf "$work/agy.tar.gz" -C "$work" antigravity
  install -d -o root -g root -m 755 "$TOOLS/agy-$AGY_VERSION"
  install -o root -g root -m 755 "$work/antigravity" "$TOOLS/agy-$AGY_VERSION/agy"
fi
ln -sfn "$TOOLS/agy-$AGY_VERSION/agy" /usr/local/bin/agy

if ! command -v docker >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq --no-install-recommends docker.io >/dev/null
fi
systemctl enable --now docker >/dev/null 2>&1 || true

cat > /etc/wsl.conf <<'EOF'
[boot]
systemd=true

[automount]
enabled=false
mountFsTab=false

[interop]
enabled=false
appendWindowsPath=false
EOF

echo "bench: $(id bench)"
echo "agy: $(/usr/local/bin/agy --version)"
echo "docker: $(docker version --format '{{.Server.Version}}')"
echo "docker の組: $(getent group docker)"
echo "wsl.conf を書いた。効かせるには Windows 側で wsl --terminate する"
