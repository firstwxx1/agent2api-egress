#!/usr/bin/env python3
"""Bind agent2api accounts to their per-account egress ports.

Reads /etc/mihomo/assignments.json  ({accountId: {slot, region, primary, backup}})
and sets each account's outbound proxy to  172.17.0.1:(21100+slot).

Default = dry-run (preview). Use --apply to write: it stops the container,
edits the SQLite DB, and starts the container again (with a DB backup first).

Env overrides:
  AGENT2API_CONTAINER (default: agent2api)
  AGENT2API_DB        (default: /opt/agent2api/data/agent2api.db)
"""
import argparse, json, os, shutil, sqlite3, subprocess, sys, time

ASG = '/etc/mihomo/assignments.json'
CONTAINER = os.environ.get('AGENT2API_CONTAINER', 'agent2api')
DB = os.environ.get('AGENT2API_DB', '/opt/agent2api/data/agent2api.db')
BASE_PORT = 21100


def build_rows():
    asg = json.load(open(ASG))
    rows = []
    for aid, cfg in asg.items():
        port = BASE_PORT + int(cfg['slot'])
        rows.append((aid, port, cfg.get('primary') or '', cfg.get('region') or ''))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='真正写入（默认只预览）')
    args = ap.parse_args()
    if not os.path.exists(ASG):
        sys.exit('缺少 %s' % ASG)
    if not os.path.exists(DB):
        sys.exit('找不到数据库 %s' % DB)

    rows = build_rows()
    con = sqlite3.connect(DB)
    print('%-10s %-6s %-8s %s' % ('账号ID', '端口', '地区', '账号名/首选'))
    plan = []
    for aid, port, primary, region in rows:
        r = list(con.execute('select data from accounts where id=?', (aid,)))
        if not r:
            print('  !! 账号不存在: %s' % aid); continue
        o = json.loads(r[0][0])
        name = o.get('name') or aid[:18]
        print('%-10s %-6d %-8s %s (%s)' % (aid[:10], port, region, name, primary))
        plan.append((aid, port, primary))

    if not args.apply:
        print('\n预览完成。加 --apply 实际写入。'); return

    ts = int(time.time())
    bak = '%s.bak-%d' % (DB, ts)
    shutil.copy2(DB, bak)
    print('\n已备份数据库: %s' % bak)

    def sh(*a):
        subprocess.run(a, check=False)

    print('停止容器 %s ...' % CONTAINER)
    sh('docker', 'stop', CONTAINER)
    con.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    n = 0
    for aid, port, primary in plan:
        r = list(con.execute('select data from accounts where id=?', (aid,)))
        if not r:
            continue
        o = json.loads(r[0][0])
        o['proxy'] = {'host': '172.17.0.1', 'port': port, 'protocol': 'http',
                      'label': '订阅·固定·' + primary, 'source': 'custom'}
        con.execute('update accounts set data=? where id=?',
                    (json.dumps(o, ensure_ascii=False), aid))
        n += 1
    con.commit()
    print('启动容器 %s ...' % CONTAINER)
    sh('docker', 'start', CONTAINER)
    print('完成：已更新 %d 个账号。' % n)


if __name__ == '__main__':
    main()
