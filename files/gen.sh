#!/bin/bash
# mihomo config refresh: regenerate from subscription, validate, hot-reload.
set -e
/usr/bin/python3 /etc/mihomo/gen.py
/usr/local/bin/mihomo -t -d /etc/mihomo >/dev/null
SECRET="$(cat /etc/mihomo/secret)"
curl -s -X PUT -H "Authorization: Bearer ${SECRET}" \
  "http://127.0.0.1:9090/configs?force=true" \
  -d '{"path":"/etc/mihomo/config.yaml"}' >/dev/null 2>&1 || systemctl restart mihomo
