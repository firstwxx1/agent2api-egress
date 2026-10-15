# agent2api-egress

把**订阅节点**做成本地出口，供 **agent2api**（WorkBuddy 本地网关）**逐账号**使用，并提供一个**可对接 agent2api 的网页插件**。

- 每个账号绑定一个**固定本地端口**（`21100 + slot`），订阅怎么变都不影响绑定；
- 每个账号一条**确定性**链路：`首选节点 → 备选节点 → 同地区节点 → 直连`；
- **自动更新**：每 6 小时拉取订阅，节点增删自动跟进；
- **看门狗**：每 2 分钟真实探针巡检，首选挂了自动切备选、恢复自动切回；
- **网页插件**：在网页里直接**导入订阅链接**、查看/切换账号出口、**自动绑定新账号**；
- 出口 IP **稳定**，避免账号因 IP 频繁跳动被风控。

> 仅供个人学习与自用；以非官方形态复用登录态可能不符合上游条款，风险自负。

## 组成

```
agent2api-egress/
├─ install.sh                 一键安装（内核 + 配置 + 插件 + systemd）
├─ files/                     运行时文件（gen.py / watchdog.py / gen.sh / systemd 单元 / nginx 片段）
├─ tools/
│  ├─ menu.py                 中文终端菜单（安装为命令 a2a-egress）
│  └─ bind-accounts.py        按 assignments 绑定账号（自带 DB 备份）
└─ plugin/
   ├─ app.py                  网页插件后端（本地 127.0.0.1:3070，含自动绑定循环）
   └─ ui.html                 插件前端（中文）
```

## 架构

```
浏览器 ──https──> nginx(/egress/) ──> 插件 app.py :3070
                                          │  订阅导入 / 账号出口 / 自动绑定
                                          ▼
agent2api 容器 (172.17.0.x)
   └─ 账号出网代理 -> http://172.17.0.1:2110N
                          ▲
宿主机 mihomo (监听 172.17.0.1)
   ├─ acct{N}  select 组: [首选, 备选, 地区·X, DIRECT]   → 端口 21100+N
   └─ 节点监听 21000+i（手动用）
驱动:
   mihomo-gen.timer        每 6h 重拉订阅、重建配置、热重载
   mihomo-watchdog.timer   每 2min 真实探针、切换首选/备选
   app.py 内 autobind 循环  每 2min 扫描新账号并自动绑定
```

关键文件（`/etc/mihomo/`）：`sub.url`（600，仅本地）、`secret`、`gen.py`、`gen.sh`、`watchdog.py`、`assignments.json`。
插件配置：`/opt/agent2api-egress/config.json`（`password` / `autobind` / `regions` / `db` / `container`）。

> mihomo 自带健康检查对 **AnyTLS / Hysteria2** 节点会误报（0ms 失败），故不用 `fallback` 自动切，改由 `watchdog.py` 真实探测驱动，更可靠。

## 安装

```bash
git clone <本仓库> && cd agent2api-egress
sudo SUB_URL='https://你的订阅/...' bash install.sh
```

安装后：
1. 把 `files/nginx-egress.snippet` 加入你的站点配置（放在 `location / {` 之前），reload nginx；
2. 浏览器打开 `https://<域名>/egress/`，用 `config.json` 里的密码登录；
3. 在插件里**导入订阅链接**、按需**自动绑定新账号**。

> 也可全用终端：命令 `a2a-egress`。

## 网页插件功能

- **订阅链接**：直接粘贴导入 / 更换，保存即刷新；
- **账号出口**：列出账号 → 当前节点 → 出口 IP → 是否绑定，一键切换出口；
- **自动绑定新账号**：开关 + 「立即扫描并绑定」；按提供商分配地区（`config.json` 的 `regions` 可改）；
  **已有自定义代理的账号不会被覆盖**。
- **运维**：刷新订阅、运行看门狗、连通性测试。

## assignments.json 格式

```json
{
  "<agent2api 账号 id>": {
    "slot": 0, "region": "香港",
    "primary": "🇨🇳香港原生htk高速",
    "backup":  "🇨🇳香港-i-Cable01"
  }
}
```

- `slot` 唯一（决定端口 `21100+slot`）；`region` 用于回退；`primary`/`backup` 为**节点名**。

## 常用操作

```bash
a2a-egress                                # 中文终端菜单
systemctl restart mihomo                  # 重启代理内核
systemctl start mihomo-gen.service        # 立即刷新订阅
python3 /etc/mihomo/watchdog.py           # 手动跑一次看门狗
systemctl restart agent2api-egress        # 重启网页插件
```

## 安全

- `sub.url`、`secret`、`config.json`、数据库**都不入库**；换机器时在本地重新填订阅链接。
- 插件默认只监听 `127.0.0.1`，经 nginx 反代访问，需密码登录。
- 上游健康检查地址用 `https://cp.cloudflare.com/generate_204`（实测 `gstatic` 不通）。

## 卸载

```bash
systemctl disable --now mihomo mihomo-gen.timer mihomo-watchdog.timer agent2api-egress
rm -rf /etc/mihomo /opt/agent2api-egress /usr/local/bin/mihomo /usr/local/bin/a2a-egress \
       /etc/systemd/system/mihomo* /etc/systemd/system/agent2api-egress.service
```
