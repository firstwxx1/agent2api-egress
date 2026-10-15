#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent2api-egress plugin · HTTP 服务

把「订阅 -> mihomo -> agent2api 逐账号出口」这套能力包成一个可对接 agent2api 的插件：
  · 在网页里直接导入/更换订阅链接
  · 查看各账号当前出口 + 出口 IP
  · 切换账号出口、手动/自动绑定新账号
  · 刷新订阅、运行看门狗、连通性测试
仅依赖 Python 标准库。以 root 运行（需要 docker / systemd / 写 DB）。
"""
import http.server, json, os, subprocess, urllib.request, threading, secrets, socketserver, sqlite3, time, re, hashlib

BASE = os.environ.get('A2AE_BASE', '/opt/agent2api-egress')
MIHOMO = '/etc/mihomo'
CFG = BASE + '/config.json'
PORT = int(os.environ.get('A2AE_PORT', '3070'))
CHECK = 'https://cp.cloudflare.com/generate_204'
IPURL = 'https://api.ipify.org'
BASE_PORT = 21100

SESSIONS = set()
LOCK = threading.Lock()

DEFAULT_CFG = {
    "password": "",
    "autobind": True,
    "db": "/opt/agent2api/data/agent2api.db",
    "container": "agent2api",
    "regions": {"workbuddy": "香港", "workbuddy-intl": "日本", "qoder": "香港",
                "autoclaw": "香港", "custom": "新加坡", "cline": "美国", "trae": "美国"},
}


def load_cfg():
    if not os.path.exists(CFG):
        c = dict(DEFAULT_CFG)
        c['password'] = secrets.token_urlsafe(9)
        save_cfg(c)
        return c
    c = dict(DEFAULT_CFG)
    c.update(json.load(open(CFG)))
    return c


def save_cfg(c):
    os.makedirs(BASE, exist_ok=True)
    json.dump(c, open(CFG, 'w'), ensure_ascii=False, indent=1)
    os.chmod(CFG, 0o600)


def load_json(path, d=None):
    try:
        return json.load(open(path))
    except Exception:
        return d


def run(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return 1, '', str(e)


def mihomo_api(path):
    sec = open(MIHOMO + '/secret').read().strip()
    req = urllib.request.Request('http://127.0.0.1:9090' + path,
                                 headers={'Authorization': 'Bearer ' + sec})
    return json.loads(urllib.request.urlopen(req, timeout=6).read())


def mihomo_put(path, body):
    sec = open(MIHOMO + '/secret').read().strip()
    req = urllib.request.Request('http://127.0.0.1:9090' + path,
                                 data=json.dumps(body).encode(), method='PUT',
                                 headers={'Authorization': 'Bearer ' + sec,
                                          'Content-Type': 'application/json'})
    return urllib.request.urlopen(req, timeout=6).read()


def probe_port(port, timeout=8):
    rc, out, _ = run(['curl', '-s', '-o', '/dev/null', '-w', '%{http_code}',
                      '--max-time', str(timeout), '-x', 'http://172.17.0.1:%d' % port, CHECK])
    return out in ('200', '204')


def exit_ip(port):
    rc, out, _ = run(['curl', '-s', '--max-time', '12', '-x', 'http://172.17.0.1:%d' % port, IPURL])
    return out or 'FAIL'


def read_accounts():
    cfg = load_cfg()
    db = cfg['db']
    if not os.path.exists(db):
        return []
    con = sqlite3.connect('file:%s?mode=ro' % db, uri=True)
    out = []
    for i, prov, data in con.execute('select id,provider,data from accounts'):
        try:
            o = json.loads(data)
        except Exception:
            continue
        p = o.get('proxy')
        out.append({'id': i, 'provider': prov, 'name': o.get('name') or i[:16],
                    'enabled': bool(o.get('enabled')),
                    'proxy': p if isinstance(p, dict) else None})
    return out


def subscribe_ids():
    asg = load_json(MIHOMO + '/assignments.json', {})
    return set(asg.keys())


def status():
    asg = load_json(MIHOMO + '/assignments.json', {})
    mp = load_json(MIHOMO + '/mapping.json', {})
    slot_of = {}
    for aid, c in asg.items():
        slot_of[aid] = c
    nodes = {k: v for k, v in mp.items() if not k.startswith('ACCT:')}
    accounts = []
    for a in read_accounts():
        c = slot_of.get(a['id'])
        slot = c['slot'] if c else None
        row = {'id': a['id'], 'name': a['name'], 'provider': a['provider'],
               'enabled': a['enabled'], 'bound': c is not None,
               'slot': slot, 'port': (BASE_PORT + slot) if slot is not None else None,
               'primary': c.get('primary') if c else None,
               'region': c.get('region') if c else None,
               'now': None, 'ok': None}
        if slot is not None:
            try:
                row['now'] = mihomo_api('/proxies/acct%d' % slot).get('now')
            except Exception:
                pass
        accounts.append(row)
    return {'sub': (open(MIHOMO + '/sub.url').read().strip() if os.path.exists(MIHOMO + '/sub.url') else ''),
            'autobind': load_cfg()['autobind'],
            'regions': load_cfg()['regions'],
            'accounts': accounts, 'nodes': list(nodes.keys()),
            'node_map': nodes}


def refresh():
    run(['systemctl', 'start', 'mihomo-gen.service'], timeout=90)
    return True


def sync_bindings():
    """Write assignments -> accounts, restarting the container once."""
    cfg = load_cfg()
    asg = load_json(MIHOMO + '/assignments.json', {})
    db = cfg['db']
    ctn = cfg['container']
    if not os.path.exists(db) or not asg:
        return '无数据'
    ts = int(time.time())
    run(['cp', db, '%s.bak-%d' % (db, ts)])
    run(['docker', 'stop', ctn], timeout=60)
    con = sqlite3.connect(db)
    con.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    n = 0
    for aid, c in asg.items():
        r = list(con.execute('select data from accounts where id=?', (aid,)))
        if not r:
            continue
        o = json.loads(r[0][0])
        o['proxy'] = {'host': '172.17.0.1', 'port': BASE_PORT + int(c['slot']),
                      'protocol': 'http', 'label': '订阅·固定·' + (c.get('primary') or ''),
                      'source': 'custom'}
        con.execute('update accounts set data=? where id=?',
                    (json.dumps(o, ensure_ascii=False), aid))
        n += 1
    con.commit()
    run(['docker', 'start', ctn], timeout=60)
    return '已绑定 %d 个账号' % n


def autobind(dry=False):
    """Assign a slot+primary+backup to any account without a 订阅 binding."""
    cfg = load_cfg()
    asg = load_json(MIHOMO + '/assignments.json', {})
    mp = load_json(MIHOMO + '/mapping.json', {})
    nodes = [k for k in mp if not k.startswith('ACCT:')]
    regions = cfg.get('regions', {})

    def region_of(name):
        for r in ['香港', '日本', '新加坡', '美国', '韩国', '台湾', '越南']:
            if r in name:
                return r
        return None

    used = {}
    for k, v in mp.items():
        if k.startswith('ACCT:') and v.get('primary'):
            used[v['primary']] = used.get(v['primary'], 0) + 1

    taken_slots = set(int(c['slot']) for c in asg.values())
    added = []

    def pick(region):
        cand = [n for n in nodes if region and region in n]
        if not cand:
            cand = nodes
        cand.sort(key=lambda n: used.get(n, 0))
        return cand

    for a in read_accounts():
        if a['id'] in asg:
            continue
        p = a['proxy']
        if p is not None and not str(p.get('label', '')).startswith('\u8ba2\u9605'):
            # respect an existing (non-subscription) proxy the user set on purpose
            continue
        region = regions.get(a['provider'])
        cand = pick(region)
        if not cand:
            continue
        primary = cand[0]
        backup = cand[1] if len(cand) > 1 else None
        slot = 0
        while slot in taken_slots:
            slot += 1
        taken_slots.add(slot)
        used[primary] = used.get(primary, 0) + 1
        asg[a['id']] = {'slot': slot, 'region': region or '其他',
                        'primary': primary, 'backup': backup}
        added.append((a['name'], a['provider'], region, primary))

    if not added:
        return []
    if not dry:
        json.dump(asg, open(MIHOMO + '/assignments.json', 'w'), ensure_ascii=False, indent=1)
        refresh()
        sync_bindings()
    return added


def set_sub(url):
    with open(MIHOMO + '/sub.url', 'w') as f:
        f.write(url.strip())
    os.chmod(MIHOMO + '/sub.url', 0o600)
    refresh()


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body=b'', ctype='application/json; charset=utf-8', extra=None):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode())

    def _authed(self):
        c = self.headers.get('Cookie', '')
        for part in c.split(';'):
            if part.strip().startswith('a2ae='):
                return part.strip()[5:] in SESSIONS
        return False

    def _body(self):
        n = int(self.headers.get('Content-Length', 0) or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode())
        except Exception:
            return {}

    def do_GET(self):
        p = self.path.split('?')[0]
        if p in ('/', '/index.html'):
            return self._send(200, open(BASE + '/ui.html', 'rb').read(),
                              'text/html; charset=utf-8')
        if p == '/api/status':
            if not self._authed():
                return self._json({'error': 'auth'}, 401)
            return self._json(status())
        if p == '/api/exit':
            if not self._authed():
                return self._json({'error': 'auth'}, 401)
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            slot = int(q.get('slot', ['-1'])[0])
            port = BASE_PORT + slot
            return self._json({'slot': slot, 'ok': probe_port(port), 'ip': exit_ip(port)})
        return self._json({'error': 'not found'}, 404)

    def do_POST(self):
        p = self.path.split('?')[0]
        b = self._body()
        if p == '/api/login':
            if b.get('password') == load_cfg()['password']:
                tok = secrets.token_urlsafe(24)
                SESSIONS.add(tok)
                return self._send(200, b'{"ok":true}', extra={'Set-Cookie': 'a2ae=' + tok + '; Path=/; HttpOnly'})
            return self._json({'error': '密码错误'}, 403)
        if p == '/api/logout':
            return self._send(200, b'{"ok":true}', extra={'Set-Cookie': 'a2ae=; Path=/; Max-Age=0'})
        if not self._authed():
            return self._json({'error': 'auth'}, 401)
        try:
            if p == '/api/sub':
                set_sub(b.get('url', ''))
                return self._json({'ok': True, 'msg': '订阅已保存并刷新'})
            if p == '/api/refresh':
                refresh()
                return self._json({'ok': True, 'msg': '已刷新订阅'})
            if p == '/api/watchdog':
                rc, out, err = run(['python3', MIHOMO + '/watchdog.py'], timeout=120)
                return self._json({'ok': True, 'msg': out or '完成'})
            if p == '/api/autobind':
                if 'enabled' in b:
                    c = load_cfg(); c['autobind'] = bool(b['enabled']); save_cfg(c)
                if b.get('run'):
                    a = autobind()
                    return self._json({'ok': True, 'msg': '已绑定 %d 个新账号' % len(a)})
                return self._json({'ok': True, 'msg': 'ok'})
            if p == '/api/switch':
                slot = int(b['slot']); name = b['target']
                mihomo_put('/proxies/acct%d' % slot, {'name': name})
                return self._json({'ok': True, 'msg': '已切换'})
            if p == '/api/sync':
                return self._json({'ok': True, 'msg': sync_bindings()})
            if p == '/api/regions':
                c = load_cfg(); c['regions'] = b.get('regions', c['regions']); save_cfg(c)
                return self._json({'ok': True, 'msg': '地区规则已保存'})
        except Exception as e:
            return self._json({'error': str(e)}, 500)
        return self._json({'error': 'not found'}, 404)


class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def autobind_loop():
    while True:
        try:
            if load_cfg().get('autobind'):
                added = autobind()
                if added:
                    print('[autobind] bound %d new accounts' % len(added), flush=True)
        except Exception as e:
            print('[autobind] error: %s' % e, flush=True)
        time.sleep(120)


if __name__ == '__main__':
    os.makedirs(BASE, exist_ok=True)
    load_cfg()
    threading.Thread(target=autobind_loop, daemon=True).start()
    print('agent2api-egress plugin on 127.0.0.1:%d' % PORT, flush=True)
    S(('127.0.0.1', PORT), H).serve_forever()
