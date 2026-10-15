#!/usr/bin/env python3
"""Regenerate mihomo config from the subscription with STABLE, account-based egress.

Key idea:
- Each agent2api account binds to a FIXED local port (21100+slot) that never changes.
- That port is backed by an account-specific `fallback` group:
      [preferred node]  ->  [region fallback group]  ->  DIRECT
  So: exit IP stays the SAME while the preferred node exists (no jumping),
  and if the subscription removes/renames that node, it auto-falls back to
  another node in the same region (keeps the region).
- Regions are recomputed every refresh, so new/removed nodes are handled automatically.
"""
import json, sys, urllib.request, yaml

CONF = '/etc/mihomo/config.yaml'
MAP = '/etc/mihomo/mapping.json'
ASG = '/etc/mihomo/assignments.json'
SUBF = '/etc/mihomo/sub.url'
SECF = '/etc/mihomo/secret'
LISTEN = '172.17.0.1'
HP = 'https://www.gstatic.com/generate_204'
ACCT_BASE = 21100
NODE_BASE = 21000

SUB = open(SUBF).read().strip()
SECRET = open(SECF).read().strip()
asg = json.load(open(ASG))

req = urllib.request.Request(SUB, headers={'User-Agent': 'mihomo/1.19.32'})
raw = urllib.request.urlopen(req, timeout=30).read().decode('utf-8', 'replace')
doc = yaml.safe_load(raw) or {}
proxies = doc.get('proxies') or []
dns = doc.get('dns') or {'enable': True, 'nameserver': ['223.5.5.5', '119.29.29.29']}
if not proxies:
    print('ERROR: no proxies in subscription', file=sys.stderr)
    sys.exit(2)
names = [p['name'] for p in proxies]
nameset = set(names)

REG = [('香港', ['香港']), ('日本', ['日本']), ('新加坡', ['新加坡']),
       ('美国', ['美国']), ('韩国', ['韩国']), ('台湾', ['台湾']), ('越南', ['越南'])]

def region_of(n):
    for r, ks in REG:
        if any(k in n for k in ks):
            return r
    return '其他'

region_nodes = {}
for n in names:
    region_nodes.setdefault(region_of(n), []).append(n)

groups = []
region_group = {}
for r, ns in region_nodes.items():
    gn = '地区·' + r
    region_group[r] = gn
    groups.append({'name': gn, 'type': 'fallback', 'proxies': ns, 'url': HP, 'interval': 300})
groups.append({'name': '自动选择', 'type': 'url-test', 'proxies': names,
               'url': HP, 'interval': 300, 'tolerance': 50})

listeners = []
mapping = {}
# per-node listeners (manual use)
for i, n in enumerate(names):
    listeners.append({'name': 'n%03d' % i, 'type': 'mixed',
                      'port': NODE_BASE + i, 'listen': LISTEN, 'proxy': n})
    mapping[n] = {'port': NODE_BASE + i, 'scheme': 'http'}

# per-account stable groups + listeners
for aid, cfg in asg.items():
    slot = cfg['slot']
    primary = cfg.get('primary')
    region = cfg.get('region')
    members = []
    if primary in nameset:
        members.append(primary)
    backup = cfg.get('backup')
    if backup and backup in nameset and backup not in members:
        members.append(backup)
    rg = region_group.get(region)
    if rg:
        members.append(rg)
    if 'DIRECT' not in members:
        members.append('DIRECT')
    gn = 'acct%d' % slot
    groups.append({'name': gn, 'type': 'select', 'proxies': members})
    port = ACCT_BASE + slot
    listeners.append({'name': gn, 'type': 'mixed', 'port': port, 'listen': LISTEN, 'proxy': gn})
    mapping['ACCT:%d' % slot] = {
        'port': port, 'scheme': 'http', 'accountId': aid,
        'primary': primary, 'region': region,
        'active': primary if primary in nameset else (rg or 'DIRECT'),
        'fallback_used': primary not in nameset,
    }

conf = {
    'mixed-port': 7890,
    'bind-address': '127.0.0.1',
    'allow-lan': False,
    'mode': 'rule',
    'log-level': 'warning',
    'ipv6': False,
    'unified-delay': True,
    'tcp-concurrent': True,
    'external-controller': '127.0.0.1:9090',
    'secret': SECRET,
    'profile': {'store-selected': True, 'store-fake-ip': True},
    'dns': dns,
    'proxies': proxies,
    'proxy-groups': groups,
    'listeners': listeners,
    'rules': ['MATCH,自动选择'],
}

with open(CONF, 'w') as f:
    yaml.dump(conf, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
with open(MAP, 'w') as f:
    json.dump(mapping, f, ensure_ascii=False, indent=1)

fb = [k for k, v in mapping.items() if k.startswith('ACCT:') and v.get('fallback_used')]
print('wrote %s: %d proxies, %d groups, %d listeners; accounts=%d; fallback=%s'
      % (CONF, len(proxies), len(groups), len(listeners),
         sum(1 for k in mapping if k.startswith('ACCT:')), fb or 'none'))
