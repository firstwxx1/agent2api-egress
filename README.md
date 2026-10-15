# agent2api-egress

把**订阅节点**做成本地出口，供 **agent2api**（WorkBuddy 本地网关）**逐账号**使用。

- 每个账号绑定一个**固定本地端口**（`21100 + slot`），订阅怎么变都不影响绑定；
- 每个账号一条**确定性**链路：`首选节点 → 备选节点 → 同地区节点 → 直连`；
- **自动更新**：每 6 小时拉取订阅，节点增删自动跟进；
- **看门狗**：每 2 分钟用真实请求巡检，首选挂了自动切备选、恢复自动切回；
- 出口 IP **稳定**，避免账号因 IP 频繁跳动被风控。

> 仅供个人学习与自用；以非官方形态复用登录态可能不符合上游条款，风险自负。

## 架构

```
agent2api 容器 (172.17.0.x)
   └─ 账号出网代理 -> http://172.17.0.1:2110N
                          ▲
宿主机 mihomo (监听 172.17.0.1)
   ├─ acct{N}  select 组: [首选, 备选, 地区·X, DIRECT]   → 端口 21100+N
   ├─ 地区·X    fallback 组: 同地区全体节点
   └─ 节点监听 21000+i（手动用）
驱动:
   mihomo-gen.timer       每 6h 重新拉订阅、重建配置、热重载
   mihomo-watchdog.timer  每 2min 真实探针、切换首选/备选
```

关键文件（`/etc/mihomo/`）：
- `sub.url`（600）订阅链接 —— **只在本地，不入库**
- `secret`（600）mihomo API 密钥
- `gen.py` 由订阅生成 config.yaml（含账号组）
- `gen.sh` 重生成 + 校验 + 热重载
- `watchdog.py` 逐账号探针与切换
- `assignments.json` 账号→{slot, region, primary, backup}

> mihomo 自带健康检查对 **AnyTLS / Hysteria2** 节点会误报（0ms 失败），故不用 `fallback` 自动切，改由 `watchdog.py` 真实探测驱动，更可靠。

## 安装

```bash
sudo SUB_URL='https://你的订阅/...' bash install.sh
# 或：sudo bash install.sh   （交互输入订阅链接）
```

安装后账号尚未绑定，编辑 `/etc/mihomo/assignments.json` 或直接用仓库里的示例，然后：

```bash
sudo python3 tools/bind-accounts.py            # 预览
sudo python3 tools/bind-accounts.py --apply    # 应用（自动备份 DB 并重启容器）
```

## assignments.json 格式

```json
{
  "<agent2api 账号 id>": {
    "slot": 0,
    "region": "香港",
    "primary": "🇨🇳香港原生htk高速",
    "backup":  "🇨🇳香港-i-Cable01"
  }
}
```

- `slot`：唯一的整数，决定端口 `21100+slot`（**同一台机器上不要重复**）。
- `region`：地区分组名（用于回退），需与节点名里的地区词一致（香港/日本/新加坡/美国/韩国/台湾/越南）。
- `primary` / `backup`：**节点名**（与订阅里一致）。名字变了会自动回退到同地区，不会断。

## 常用操作

```bash
systemctl restart mihomo                 # 重启代理内核
systemctl start mihomo-gen.service       # 立即刷新订阅
python3 /etc/mihomo/watchdog.py          # 手动跑一次看门狗
curl -s -H "Authorization: Bearer $(cat /etc/mihomo/secret)" \
     http://127.0.0.1:9090/proxies/acct0  # 看某账号当前出口
```

## 说明

- `sub.url`、`secret`、数据库均**不入库**；换机器时在本地重新填写订阅链接即可。
- 上游节点健康检查地址用 `https://cp.cloudflare.com/generate_204`（实测 `gstatic` 不通）。
- 卸载：`systemctl disable --now mihomo mihomo-gen.timer mihomo-watchdog.timer && rm -rf /etc/mihomo /usr/local/bin/mihomo /etc/systemd/system/mihomo*`
