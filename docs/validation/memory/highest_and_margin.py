"""Highest memory of each container (docker stats, MiB) from the two kept series, x 1.15, rounded up to a whole MiB; writes
docs/validation/memory_limits.md-ready rows (json) and edits compose.yaml.
usage: mem_calc.py FULL_STACK_CSV LOAD_RUN_DIR"""
import csv
import glob
import json
import math
import re
import sys

full_csv, load_dir = sys.argv[1], sys.argv[2]


def mib(s):
    m = re.match(r'([\d.]+)\s*([KMG]i?B|B)', s)
    return float(m.group(1)) * {'KiB': 1 / 1024, 'MiB': 1, 'GiB': 1024, 'B': 1 / 1048576}[m.group(2)]


peak = {}     # service -> (value, where)
for r in csv.DictReader(open(full_csv, encoding='utf-8')):
    s = re.sub(r'^camera-cloud-core-|-1$', '', r['container'])
    v = mib(r['mem_usage'].split('/')[0].strip())
    if v > peak.get(s, (0, ''))[0]:
        peak[s] = (v, 'integration runs')
for f in sorted(glob.glob(load_dir + '/run*/containers.csv')):
    for r in csv.DictReader(open(f, encoding='utf-8')):
        s = re.sub(r'^camera-cloud-core-|-1$', '', r['container'])
        if s.startswith('HOST'):
            continue
        v = float(r['mem_mib'])
        if v > peak.get(s, (0, ''))[0]:
            peak[s] = (v, 'load test')
out = {k: [round(v, 1), w] for k, (v, w) in sorted(peak.items())}
json.dump(out, open(sys.argv[3], 'w'), indent=1)
for k, (v, w) in sorted(peak.items()):
    print(f"{k:14s} {v:8.1f} {w:18s} x1.15 = {v * 1.15:8.2f} -> {math.ceil(v * 1.15)}m")
