#!/usr/bin/env python3
"""Controlled egress watchdog for per-account groups.

mihomo's own health-check gives false negatives for some node types
(AnyTLS/Hysteria2), so we drive the groups ourselves with REAL probes:

- Probe each account's PRIMARY node directly (its own listener port).
- If primary is healthy  -> force the account's select-group to primary (stable IP).
- If primary is dead     -> force it to the BACKUP node (if that is healthy).
This keeps exit IPs stable and failover explicit/controlled.
"""
import json, subprocess, urllib.request

SEC = open('/etc/mihomo/secret').read().strip()
MAP = json.load(open('/etc/mihomo/mapping.json'))
ASG = json.load(open('/etc/mihomo/assignments.json'))
API = 'http://127.0.0.1:9090'
CHECK = 'https://cp.cloudflare.com/generate_204'


def probe(port, timeout=8, tries=2):
    if not port:
        return False
    for _ in range(tries):
        r = subprocess.run(['curl', '-s', '-o', '/dev/null', '-w', '%{http_code}',
                            '--max-time', str(timeout), '-x', 'http://172.17.0.1:%d' % port, CHECK],
                           capture_output=True, text=True)
        if r.stdout.strip() in ('200', '204'):
            return True
    return False


def api_get(path):
    req = urllib.request.Request(API + path, headers={'Authorization': 'Bearer ' + SEC})
    return json.loads(urllib.request.urlopen(req, timeout=5).read())


def set_sel(group, name):
    body = json.dumps({'name': name}).encode()
    req = urllib.request.Request(API + '/proxies/' + group, data=body, method='PUT',
                                 headers={'Authorization': 'Bearer ' + SEC,
                                          'Content-Type': 'application/json'})
    urllib.request.urlopen(req, timeout=5)


changed = []
for aid, cfg in ASG.items():
    group = 'acct%d' % cfg['slot']
    primary = cfg.get('primary')
    backup = cfg.get('backup')
    pport = (MAP.get(primary) or {}).get('port')
    bport = (MAP.get(backup) or {}).get('port')

    try:
        cur = api_get('/proxies/' + group).get('now')
    except Exception:
        cur = None

    prim_ok = probe(pport)
    if prim_ok:
        target = primary
    elif probe(bport):
        target = backup
    else:
        target = None

    if target and cur != target:
        try:
            set_sel(group, target)
            changed.append('%s: %s -> %s' % (group, cur, target))
        except Exception as e:
            changed.append('%s: switch error %s' % (group, e))

if changed:
    print('\n'.join(changed))
else:
    print('all good')
