#!/usr/bin/env bash
# =============================================================================
# agent2api-egress 一键安装脚本
#   在服务器上部署 mihomo，把订阅节点做成本地出口，供 agent2api 逐账号使用：
#     - 每个账号绑定一个固定本地端口 (21100+slot)
#     - 首选节点 -> 备选节点 -> 同地区 -> 直连（确定性切换）
#     - 每 6 小时自动刷新订阅；看门狗每 2 分钟巡检并切换
#   订阅链接等敏感信息只在本地写入，不进入仓库。
#
#   用法：  sudo SUB_URL='https://...' bash install.sh
#       或  sudo bash install.sh   （随后交互输入订阅链接）
#
#   环境变量：
#     SUB_URL        订阅链接（Clash YAML）；缺省时交互输入
#     MIHOMO_VER     mihomo 版本，默认 v1.19.32
# =============================================================================
set -euo pipefail

if [[ "$(id -u)" != "0" ]]; then
  echo "请以 root 运行： sudo SUB_URL='...' bash install.sh" >&2
  exit 1
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/files"
MIHOMO_VER="${MIHOMO_VER:-v1.19.32}"

SUB_URL="${SUB_URL:-}"
if [[ -z "$SUB_URL" ]]; then
  read -rp "请输入订阅链接 (Clash YAML): " SUB_URL
fi
[[ -n "$SUB_URL" ]] || { echo "缺少订阅链接" >&2; exit 1; }

echo "[1/6] 安装 mihomo ($MIHOMO_VER) ..."
if ! command -v mihomo >/dev/null 2>&1; then
  tmp="$(mktemp -d)"
  url="https://github.com/MetaCubeX/mihomo/releases/download/${MIHOMO_VER}/mihomo-linux-amd64-compatible-${MIHOMO_VER}.gz"
  curl -fsSL -o "$tmp/m.gz" "$url" || curl -fsSL -o "$tmp/m.gz" "https://gh-proxy.com/$url"
  gunzip -f "$tmp/m.gz"
  install -m 0755 "$tmp/m" /usr/local/bin/mihomo
  rm -rf "$tmp"
fi
/usr/local/bin/mihomo -v | head -1

echo "[2/6] 写入配置到 /etc/mihomo ..."
install -d -m 700 /etc/mihomo
printf '%s' "$SUB_URL" > /etc/mihomo/sub.url && chmod 600 /etc/mihomo/sub.url
if [[ ! -f /etc/mihomo/secret ]]; then openssl rand -hex 16 > /etc/mihomo/secret; fi
chmod 600 /etc/mihomo/secret
install -m 0755 "$SRC/gen.py"      /etc/mihomo/gen.py
install -m 0755 "$SRC/gen.sh"      /etc/mihomo/gen.sh
install -m 0755 "$SRC/watchdog.py" /etc/mihomo/watchdog.py
if [[ ! -f /etc/mihomo/assignments.json ]]; then
  install -m 0644 "$SRC/assignments.json" /etc/mihomo/assignments.json
  echo "  · 已写入示例 assignments.json（请按需改成你的账号→节点）"
fi

echo "[3/6] 生成 mihomo 配置 ..."
python3 /etc/mihomo/gen.py

echo "[4/6] 安装 systemd 单元 ..."
install -m 0644 "$SRC/mihomo.service"           /etc/systemd/system/mihomo.service
install -m 0644 "$SRC/mihomo-gen.service"       /etc/systemd/system/mihomo-gen.service
install -m 0644 "$SRC/mihomo-gen.timer"         /etc/systemd/system/mihomo-gen.timer
install -m 0644 "$SRC/mihomo-watchdog.service"  /etc/systemd/system/mihomo-watchdog.service
install -m 0644 "$SRC/mihomo-watchdog.timer"    /etc/systemd/system/mihomo-watchdog.timer
systemctl daemon-reload

echo "[5/6] 启动服务与定时器 ..."
systemctl enable --now mihomo
systemctl enable --now mihomo-gen.timer
systemctl enable --now mihomo-watchdog.timer
sleep 2
systemctl is-active mihomo

echo "[6/6] 完成 ✅"
cat <<'TIP'

出口端口：每个账号 = 21100 + slot  (绑定在 172.17.0.1，供 agent2api 容器访问)
下一步：把 agent2api 账号逐个绑到对应端口，运行：
    sudo python3 tools/bind-accounts.py            # 预览
    sudo python3 tools/bind-accounts.py --apply    # 应用（会先备份 DB 并重启容器）
（容器名/DB 路径可用环境变量 AGENT2API_CONTAINER / AGENT2API_DB 覆盖）
TIP
