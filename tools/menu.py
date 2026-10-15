#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent2api 出口管理 · 中文终端菜单

查看/切换各账号出口、刷新订阅、看门狗、连通性测试。
只用本地文件 + mihomo API + agent2api 数据库（只读），不联网除节点本身。
"""
import json, os, subprocess, sys, time

MIHOMO = '/etc/mihomo'
SEC = open(MIHOMO + '/secret').read().strip()
API = 'http://127.0.0.1:9090'
CHECK = 'https://cp.cloudflare.com/generate_204'
IPURL = 'https://api.ipify.org'
DB = os.environ.get('AGENT2API_DB', '/opt/agent2api/data/agent2api.db')
BASE_PORT = 21100


def load():
    MAP = json.load(open(MIHOMO + '/mapping.json'))
    ASG = json.load(open(MIHOMO + '/assignments.json'))
    return MAP, ASG


def api_get(path):
    import urllib.request
    req = urllib.request.Request(API + path, headers={'Authorization': 'Bearer ' + SEC})
    return json.loads(urllib.request.urlopen(req, timeout=6).read())


def api_put(path, body):
    import urllib.request
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(),
                                 method='PUT',
                                 headers={'Authorization': 'Bearer ' + SEC,
                                          'Content-Type': 'application/json'})
    return urllib.request.urlopen(req, timeout=6).read()


def probe(port, url=CHECK, timeout=10):
    r = subprocess.run(['curl', '-s', '-o', '/dev/null', '-w', '%{http_code}',
                        '--max-time', str(timeout), '-x', 'http://172.17.0.1:%d' % port, url],
                       capture_output=True, text=True)
    return r.stdout.strip() in ('200', '204')


def exit_ip(port):
    r = subprocess.run(['curl', '-s', '--max-time', '12',
                        '-x', 'http://172.17.0.1:%d' % port, IPURL],
                       capture_output=True, text=True)
    return r.stdout.strip() or 'FAIL'


def acct_names():
    try:
        import sqlite3
        c = sqlite3.connect('file:' + DB + '?mode=ro', uri=True)
        d = {}
        for i, data in c.execute('select id,data from accounts'):
            try:
                o = json.loads(data)
            except Exception:
                continue
            p = o.get('proxy')
            if isinstance(p, dict) and str(p.get('label', '')).startswith('订阅'):
                d[i] = o.get('name') or i[:16]
        return d
    except Exception:
        return {}


def by_slot(ASG):
    out = {}
    for aid, cfg in ASG.items():
        out[cfg['slot']] = (aid, cfg)
    return out


def show_status():
    MAP, ASG = load()
    names = acct_names()
    slots = by_slot(ASG)
    print('\n%-4s %-20s %-10s %-22s %s' % ('槽', '账号', '端口', '当前出口', '出口 IP'))
    print('-' * 82)
    for slot in sorted(slots):
        aid, cfg = slots[slot]
        g = 'acct%d' % slot
        try:
            now = api_get('/proxies/' + g).get('now')
        except Exception:
            now = '(不可读)'
        port = BASE_PORT + slot
        ip = exit_ip(port) if probe(port) else 'FAIL'
        mark = '' if now == cfg.get('primary') else '  ← 非首选'
        print('%-4d %-20s %-10d %-22s %s%s' % (slot, names.get(aid, aid[:16]),
              port, str(now), ip, mark))
    print()


def switch_slot():
    MAP, ASG = load()
    names = acct_names()
    slots = by_slot(ASG)
    show_status()
    try:
        slot = int(input('要切换的「槽」编号: ').strip())
    except Exception:
        return
    if slot not in slots:
        print('没有这个槽'); return
    aid, cfg = slots[slot]
    g = 'acct%d' % slot
    try:
        allm = api_get('/proxies/' + g).get('all') or []
    except Exception:
        allm = []
    print('\n可选出口：')
    for i, m in enumerate(allm, 1):
        print('  %d) %s' % (i, m))
    try:
        k = int(input('选择编号（回车取消）: ').strip())
    except Exception:
        return
    if not (1 <= k <= len(allm)):
        return
    tgt = allm[k - 1]
    api_put('/proxies/' + g, {'name': tgt})
    print('✅ %s(%s) 出口已切到：%s' % (names.get(aid, aid[:16]), g, tgt))


def list_nodes():
    MAP, ASG = load()
    print('\n订阅节点：')
    for n, v in MAP.items():
        if n.startswith('ACCT:'):
            continue
        print('  %-6s %s' % (v.get('port'), n))
    print()


def refresh():
    print('刷新订阅中 ...')
    subprocess.run(['systemctl', 'start', 'mihomo-gen.service'])
    time.sleep(1)
    print('✅ 已刷新（生成 + 热重载）')


def watchdog():
    print('运行看门狗 ...')
    subprocess.run(['python3', MIHOMO + '/watchdog.py'])
    print('✅ 完成')


def test_all():
    MAP, ASG = load()
    slots = by_slot(ASG)
    print('\n连通性测试：')
    for slot in sorted(slots):
        port = BASE_PORT + slot
        ok = probe(port)
        print('  acct%-2d :2110%d  %s' % (slot, slot, 'OK' if ok else 'FAIL'))
    print()


def main():
    while True:
        print('===== agent2api 出口管理 =====')
        print(' 1) 查看各账号当前出口（含出口 IP）')
        print(' 2) 切换账号出口（首选 / 备选 / 指定节点）')
        print(' 3) 查看订阅节点列表')
        print(' 4) 立即刷新订阅')
        print(' 5) 立即运行看门狗')
        print(' 6) 测试全部账号连通性')
        print(' 0) 退出')
        c = input('选择: ').strip()
        try:
            if c == '1':
                show_status()
            elif c == '2':
                switch_slot()
            elif c == '3':
                list_nodes()
            elif c == '4':
                refresh()
            elif c == '5':
                watchdog()
            elif c == '6':
                test_all()
            elif c == '0' or c == '':
                break
        except KeyboardInterrupt:
            print()
            break
        except Exception as e:
            print('出错：%s' % e)


if __name__ == '__main__':
    main()
