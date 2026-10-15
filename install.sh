#!/usr/bin/env bash
# =============================================================================
# agent2api-egress 一键安装
#   订阅 -> mihomo -> agent2api 逐账号出口，并提供一个可对接 agent2api 的网页插件：
#     · 网页里直接导入/更换订阅链接
#     · 查看各账号当前出口 + 出口 IP
#     · 切换账号出口、手动/自动绑定新账号
#     · 刷新订阅、运行看门狗、连通性测试
#   细分能力：每账号固定端口(21100+slot)、首选->备选->同地区->直连、
#             每 6h 刷新订阅、每 2min 看门狗巡检。
#   订阅链接等敏感信息只在本地写入，不进入仓库。
#
#   用法：  sudo SUB_URL='https://...' bash install.sh
#       或  sudo bash install.sh        （随后交互输入订阅链接）
#
#   环境变量：SUB_URL、MIHOMO_VER（默认 v1.19.32）、PLUGIN_PORT（默认 3070）
# =============================================================================
set -euo pipefail

if [[ "$(id -u)" != "0" ]]; then echo "请以 root 运行： sudo SUB_URL='...' bash install.sh" >&2; exit 1; fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/files"
PLUGIN_SRC="$HERE/plugin"
MIHOMO_VER="${MIHOMO_VER:-v1.19.32}"
PLUGIN_PORT="${PLUGIN_PORT:-3070}"

SUB_URL="${SUB_URL:-}"
[[ -n "$SUB_URL" ]] || read -rp "请输入订阅链接 (Clash YAML): " SUB_URL
[[ -n "$SUB_URL" ]] || { echo "缺少订阅链接" >&2; exit 1; }

echo "[1/7] 安装 mihomo ($MIHOMO_VER) ..."
if ! command -v mihomo >/dev/null 2>&1; then
  tmp="$(mktemp -d)"
  url="https://github.com/MetaCubeX/mihomo/releases/download/${MIHOMO_VER}/mihomo-linux-amd64-compatible-${MIHOMO_VER}.gz"
  curl -fsSL -o "$tmp/m.gz" "$url" || curl -fsSL -o "$tmp/m.gz" "https://gh-proxy.com/$url"
  gunzip -f "$tmp/m.gz"; install -m 0755 "$tmp/m" /usr/local/bin/mihomo; rm -rf "$tmp"
fi
/usr/local/bin/mihomo -v | head -1

echo "[2/7] 写入 mihomo 配置 (/etc/mihomo) ..."
install -d -m 700 /etc/mihomo
printf '%s' "$SUB_URL" > /etc/mihomo/sub.url && chmod 600 /etc/mihomo/sub.url
[[ -f /etc/mihomo/secret ]] || { openssl rand -hex 16 > /etc/mihomo/secret; }
chmod 600 /etc/mihomo/secret
install -m 0755 "$SRC/gen.py"      /etc/mihomo/gen.py
install -m 0755 "$SRC/gen.sh"      /etc/mihomo/gen.sh
install -m 0755 "$SRC/watchdog.py" /etc/mihomo/watchdog.py
install -m 0755 "$HERE/tools/menu.py" /etc/mihomo/menu.py
ln -sf /etc/mihomo/menu.py /usr/local/bin/a2a-egress
if [[ ! -f /etc/mihomo/assignments.json ]]; then
  install -m 0644 "$SRC/assignments.json" /etc/mihomo/assignments.json
  echo "  · 已写入示例 assignments.json（可改，或留空让插件自动绑定）"
fi

echo "[3/7] 生成 mihomo 配置 ..."
python3 /etc/mihomo/gen.py

echo "[4/7] 安装网页插件 (/opt/agent2api-egress) ..."
install -d -m 700 /opt/agent2api-egress
install -m 0755 "$PLUGIN_SRC/app.py" /opt/agent2api-egress/app.py
install -m 0644 "$PLUGIN_SRC/ui.html" /opt/agent2api-egress/ui.html
if [[ ! -f /opt/agent2api-egress/config.json ]]; then
  python3 - "$PLUGIN_PORT" <<'PY'
import json,sys,secrets
json.dump({"password":secrets.token_urlsafe(9),"autobind":True,
 "db":"/opt/agent2api/data/agent2api.db","container":"agent2api",
 "regions":{"workbuddy":"香港","workbuddy-intl":"日本","qoder":"香港","autoclaw":"香港","custom":"新加坡"}},
 open("/opt/agent2api-egress/config.json","w"),ensure_ascii=False,indent=1)
PY
  chmod 600 /opt/agent2api-egress/config.json
  echo "  · 插件登录密码（请记下）：$(python3 -c "import json;print(json.load(open('/opt/agent2api-egress/config.json'))['password'])")"
fi

echo "[5/7] 安装 systemd 单元 ..."
install -m 0644 "$SRC/mihomo.service"           /etc/systemd/system/mihomo.service
install -m 0644 "$SRC/mihomo-gen.service"       /etc/systemd/system/mihomo-gen.service
install -m 0644 "$SRC/mihomo-gen.timer"         /etc/systemd/system/mihomo-gen.timer
install -m 0644 "$SRC/mihomo-watchdog.service"  /etc/systemd/system/mihomo-watchdog.service
install -m 0644 "$SRC/mihomo-watchdog.timer"    /etc/systemd/system/mihomo-watchdog.timer
install -m 0644 "$SRC/agent2api-egress.service" /etc/systemd/system/agent2api-egress.service
systemctl daemon-reload

echo "[6/7] 启动服务与定时器 ..."
systemctl enable --now mihomo
systemctl enable --now mihomo-gen.timer
systemctl enable --now mihomo-watchdog.timer
systemctl enable --now agent2api-egress
sleep 2
systemctl is-active mihomo agent2api-egress

echo "[7/7] 完成 ✅"
cat <<TIP

中文终端菜单：  a2a-egress
网页插件：      http://127.0.0.1:${PLUGIN_PORT}  (经反代访问 https://<域名>/egress/)
               · 把 files/nginx-egress.snippet 加进你的站点配置（放在 location / 之前）即可公网访问
               · 登录密码见 /opt/agent2api-egress/config.json

绑定账号（可选；插件里也能一键自动绑定）：
    sudo python3 tools/bind-accounts.py            # 预览
    sudo python3 tools/bind-accounts.py --apply    # 应用（自动备份 DB 并重启容器）
（容器名/DB 路径可用 AGENT2API_CONTAINER / AGENT2API_DB 覆盖）

自动绑定：插件默认开启，每 2 分钟扫描新账号并按其提供商分配地区节点；
         已有自定义代理的账号不会被覆盖。
TIP
