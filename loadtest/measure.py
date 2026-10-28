"""Runs the measured sessions from the host and turns their files into tables. It only calls `docker compose`
(exec / cp), `docker stats` and `docker inspect`; it builds and pulls nothing.

  python loadtest/measure.py run --label v1 [--runs 3] [--seconds 340] [--files compose.yaml compose.load.yaml ...]
  python loadtest/measure.py summarize --label v1
  python loadtest/measure.py compare --label v2      (the first version's round is v1)
  python loadtest/measure.py clean

`run` creates the load data once (setup_load.py), then runs `--runs` sessions one after the other. In each session it
starts the lag probe and Locust together, records every container's CPU and memory every 10 seconds, and reads
PostgreSQL's counters before and after. Before each session the load tenants' segment and state rows are cleared, so
every session starts from the same table sizes. At the end it removes the load data. Results: loadtest/out/<label>/.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import pathlib
import statistics
import subprocess
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "loadtest" / "out"
FILES = ["compose.yaml", "compose.load.yaml"]     # --files adds or replaces them (the later version adds its limits)
COMPOSE: list[str] = []


def set_files(files: list[str]) -> None:
    COMPOSE[:] = ["docker", "compose", *[x for f in files for x in ("-f", f)], "--profile", "load"]


set_files(FILES)
USERS = 50 + 20 + 300
SAMPLE_EVERY = 10
LOAD_TENANTS = "(SELECT id FROM tenants WHERE name LIKE 'load-%')"


def sh(cmd: list[str], *, check: bool = True, capture: bool = True, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, check=check, capture_output=capture, text=True, encoding="utf-8", **kw)


def compose(*args: str, **kw) -> subprocess.CompletedProcess:
    return sh([*COMPOSE, *args], **kw)


def psql(sql: str) -> str:
    return compose("exec", "-T", "postgres", "psql", "-U", "ccc", "-d", "ccc", "-v", "ON_ERROR_STOP=1", "-At",
                   "-F", ",", "-c", sql).stdout.strip()


def clear_run_rows() -> None:
    psql(f"DELETE FROM recording_segments WHERE tenant_id IN {LOAD_TENANTS}; "
         f"DELETE FROM camera_status WHERE tenant_id IN {LOAD_TENANTS};")


def drop_load_data() -> None:
    """The API only marks a camera deleted and has no tenant removal, so the rows are removed here, last of all."""
    psql(f"""BEGIN; CREATE TEMP TABLE t AS SELECT id FROM tenants WHERE name LIKE 'load-%';
DELETE FROM recording_segments WHERE tenant_id IN (SELECT id FROM t);
DELETE FROM camera_status WHERE tenant_id IN (SELECT id FROM t);
DELETE FROM cameras WHERE tenant_id IN (SELECT id FROM t);
DELETE FROM memberships WHERE tenant_id IN (SELECT id FROM t);
DELETE FROM tenants WHERE id IN (SELECT id FROM t); COMMIT;""")


def pg_counters() -> dict:
    row = psql("SELECT tup_inserted, tup_updated, xact_commit FROM pg_stat_database WHERE datname = 'ccc'")
    ins, upd, xact = (int(x) for x in row.split(","))
    return {"tup_inserted": ins, "tup_updated": upd, "xact_commit": xact}


def host_cpu() -> float | None:
    r = sh(["powershell", "-NoProfile", "-Command",
            "(Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -Filter \"Name='_Total'\").PercentProcessorTime"],
           check=False)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def _mib(text: str) -> float:
    used = text.split("/")[0].strip()
    for unit, k in (("GiB", 1024), ("MiB", 1), ("KiB", 1 / 1024), ("B", 1 / 1048576)):
        if used.endswith(unit):
            return float(used[: -len(unit)]) * k
    return float("nan")


class Sampler(threading.Thread):
    """Every 10 seconds: CPU (percent of one CPU) and memory of each container, and the host's total CPU."""

    def __init__(self, path: pathlib.Path) -> None:
        super().__init__(daemon=True)
        self.path, self.stop_flag = path, threading.Event()

    def run(self) -> None:
        with self.path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["time_utc", "container", "cpu_percent", "mem_mib"])
            while not self.stop_flag.is_set():
                t0 = time.monotonic()
                now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
                r = sh(["docker", "stats", "--no-stream", "--format", "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}"],
                       check=False)
                for line in r.stdout.splitlines():
                    name, cpu, mem = line.split("|")
                    w.writerow([now, name, cpu.rstrip("%"), f"{_mib(mem):.1f}"])
                hc = host_cpu()
                if hc is not None:
                    w.writerow([now, "HOST_total_cpu_percent_of_all_cores", f"{hc}", ""])
                f.flush()
                self.stop_flag.wait(max(0.0, SAMPLE_EVERY - (time.monotonic() - t0)))


def environment() -> dict:
    info = json.loads(sh(["docker", "info", "--format", "{{json .}}"]).stdout)
    names = sh(["docker", "ps", "--format", "{{.Names}}"]).stdout.split()
    limits = {}
    for n in names:
        d = json.loads(sh(["docker", "inspect", n]).stdout)[0]
        h = d["HostConfig"]
        limits[n] = {"image_id": d["Image"], "cpus": h["NanoCpus"] / 1e9 if h["NanoCpus"] else None,
                     "memory_mib": h["Memory"] / 1048576 if h["Memory"] else None}
    wsl = pathlib.Path.home() / ".wslconfig"
    return {"time_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "docker_cpus": info["NCPU"], "docker_memory_bytes": info["MemTotal"],
            "wslconfig": ("\n".join(l for l in wsl.read_text(encoding="utf-8").splitlines()
                                    if l.strip() and not l.lstrip().startswith("#")) if wsl.exists() else None),
            "containers": limits}


def run(label: str, runs: int, seconds: int) -> None:
    out = OUT / label
    out.mkdir(parents=True, exist_ok=True)
    (out / "environment.json").write_text(json.dumps(environment(), indent=2), encoding="utf-8")
    compose("exec", "-T", "locust", "python", "/app/setup_load.py", capture=False)
    try:
        for n in range(1, runs + 1):
            rid = f"{label}-{n}-{int(time.time())}"
            d = out / f"run{n}"
            d.mkdir(exist_ok=True)
            remote = f"/work/out/{rid}"
            compose("exec", "-T", "locust", "mkdir", "-p", remote)
            clear_run_rows()
            before = pg_counters()
            sampler = Sampler(d / "containers.csv")
            sampler.start()
            t0 = time.time()
            compose("exec", "-T", "-d", "-e", f"RUN_ID={rid}", "locust", "python", "/app/probe.py", "--seconds",
                    str(seconds), "--out", f"{remote}/probe.csv")
            with (d / "locust.log").open("w", encoding="utf-8") as log:
                p = subprocess.run([*COMPOSE, "exec", "-T", "-e", f"RUN_ID={rid}", "locust", "locust", "-f",
                                    "/app/locustfile.py", "--headless", "-u", str(USERS), "-r", "10", "-t",
                                    f"{seconds}s", "--processes", "2", "--reset-stats", "--csv", f"{remote}/locust",
                                    "--csv-full-history", "--only-summary", "--loglevel", "WARNING",
                                    "--exit-code-on-error", "0"],
                                   cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, text=True)
            time.sleep(3)  # the probe's last row
            after = pg_counters()
            elapsed = time.time() - t0
            sampler.stop_flag.set()
            sampler.join(timeout=30)
            compose("cp", f"locust:{remote}/.", str(d))
            (d / "run.json").write_text(json.dumps({
                "run_id": rid, "start_epoch": t0, "elapsed_s": elapsed, "locust_exit_code": p.returncode, "pg_before": before,
                "pg_after": after, "host_time_utc": dt.datetime.fromtimestamp(t0, dt.timezone.utc).isoformat()},
                indent=2), encoding="utf-8")
            print(f"run {n} done: locust exit {p.returncode}", flush=True)
            time.sleep(20)
    finally:
        compose("exec", "-T", "locust", "python", "/app/setup_load.py", "--remove", check=False)
        drop_load_data()


# ---------------------------------------------------------------- tables

def _rows(path: pathlib.Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _full_load_epoch(d: pathlib.Path) -> float:
    """When all the users were running (the first history row with the full user count)."""
    for r in _rows(d / "locust_stats_history.csv"):
        if r["Name"] == "Aggregated" and r["User Count"] == str(USERS):
            return float(r["Timestamp"])
    raise SystemExit(f"{d}: the full user count was never reached")


def _med(xs: list[float]) -> str:
    return f"{statistics.median(xs):.1f}" if xs else "-"


def _span(xs: list[float], digits: int = 1) -> str:
    if not xs:
        return "-"
    return f"{statistics.median(xs):.{digits}f} ({min(xs):.{digits}f} to {max(xs):.{digits}f})"


def collect(label: str) -> dict:
    """The numbers of all the sessions of one round, as lists with one value for each session."""
    out = OUT / label
    runs = sorted(p for p in out.glob("run*") if p.is_dir())
    per_name: dict[str, dict[str, list[float]]] = {}
    lag: dict[str, dict[str, list[float]]] = {"segment": {"p50": [], "p95": [], "max": [], "n": [], "timeouts": []},
                                              "state": {"p50": [], "p95": [], "max": [], "n": [], "timeouts": []}}
    cpu: dict[str, list[float]] = {}
    mem: dict[str, list[float]] = {}
    host: list[float] = []
    host_mean: list[float] = []
    host_max: list[float] = []
    pg: dict[str, list[float]] = {"writes_per_s": [], "commits_per_s": []}
    for d in runs:
        t_full = _full_load_epoch(d)
        meta = json.loads((d / "run.json").read_text(encoding="utf-8"))
        for r in _rows(d / "locust_stats.csv"):
            name = ("TOTAL" if r["Name"] == "Aggregated" else
                    r["Name"] if r["Name"].startswith(r["Type"] + " ") else f"{r['Type']} {r['Name']}")
            n, fails = int(r["Request Count"]), int(r["Failure Count"])
            m = per_name.setdefault(name, {k: [] for k in ("rps", "p50", "p95", "fail_pct", "n")})
            m["rps"].append(float(r["Requests/s"]))
            m["p50"].append(float(r["50%"]))
            m["p95"].append(float(r["95%"]))
            m["fail_pct"].append(100.0 * fails / n if n else 0.0)
            m["n"].append(n)
        for kind in ("segment", "state"):
            rows = [r for r in _rows(d / "probe.csv") if r["kind"] == kind and
                    dt.datetime.fromisoformat(r["time_utc"]).timestamp() >= t_full]
            ok = sorted(float(r["lag_ms"]) for r in rows if r["result"] == "ok")
            lag[kind]["n"].append(len(rows))
            lag[kind]["timeouts"].append(len(rows) - len(ok))
            if ok:
                lag[kind]["p50"].append(statistics.median(ok))
                lag[kind]["p95"].append(ok[min(len(ok) - 1, int(0.95 * len(ok)))])
                lag[kind]["max"].append(ok[-1])
        by_c: dict[str, list[tuple[float, float]]] = {}
        for r in _rows(d / "containers.csv"):
            t = dt.datetime.fromisoformat(r["time_utc"]).timestamp()
            if not (t_full <= t <= t_full + 300):
                continue
            if r["container"].startswith("HOST_"):
                host.append(float(r["cpu_percent"]))
            elif r["mem_mib"] != "":
                by_c.setdefault(r["container"], []).append((float(r["cpu_percent"]), float(r["mem_mib"])))
        if host:
            host_mean.append(statistics.mean(host))
            host_max.append(max(host))
            host.clear()
        for c, xs in by_c.items():
            cpu.setdefault(c, []).append(statistics.mean(x[0] for x in xs) / 100)
            mem.setdefault(c, []).append(max(x[1] for x in xs))
        b, a = meta["pg_before"], meta["pg_after"]
        secs = float(meta["elapsed_s"])
        pg["writes_per_s"].append(((a["tup_inserted"] + a["tup_updated"]) - (b["tup_inserted"] + b["tup_updated"])) / secs)
        pg["commits_per_s"].append((a["xact_commit"] - b["xact_commit"]) / secs)

    return {"runs": runs, "per_name": per_name, "lag": lag, "cpu": cpu, "mem": mem, "host_mean": host_mean,
            "host_max": host_max, "pg": pg}


def summarize(label: str) -> str:
    c = collect(label)
    runs, per_name, lag, cpu, mem = c["runs"], c["per_name"], c["lag"], c["cpu"], c["mem"]
    host_mean, host_max, pg = c["host_mean"], c["host_max"], c["pg"]
    out = OUT / label
    lines: list[str] = []
    lines += [f"### Locust results ({len(runs)} sessions; median, then the lowest to highest in brackets)", "",
              "| Path | Requests per second | p50 (ms) | p95 (ms) | Failures (%) |", "|---|---|---|---|---|"]
    for name, m in per_name.items():
        lines.append(f"| {name} | {_span(m['rps'])} | {_span(m['p50'], 0)} | {_span(m['p95'], 0)} | "
                     f"{_span(m['fail_pct'], 2)} |")
    lines += ["", "### Time until the API shows the change (probe, ms)", "",
              "| Probe | Probes per session | Timeouts per session | p50 | p95 | Slowest |", "|---|---|---|---|---|---|"]
    for kind, m in lag.items():
        lines.append(f"| {kind} | {_span(m['n'], 0)} | {_span(m['timeouts'], 0)} | {_span(m['p50'], 0)} | "
                     f"{_span(m['p95'], 0)} | {_span(m['max'], 0)} |")
    lines += ["", "### Containers while all users were running (mean CPU in CPUs, highest memory in MiB)", "",
              "| Container | CPU | Memory |", "|---|---|---|"]
    for c in sorted(cpu):
        lines.append(f"| {c} | {_span(cpu[c], 2)} | {_span(mem[c], 0)} |")
    lines += ["", f"The whole host (all its logical processors) while all users were running: mean {_span(host_mean, 0)} %, "
              f"highest sample {_span(host_max, 0)} %."]
    lines += ["", "### PostgreSQL over a whole session (per second)", "",
              "| Counter | Value |", "|---|---|",
              f"| tuples inserted + updated | {_span(pg['writes_per_s'], 0)} |",
              f"| committed transactions | {_span(pg['commits_per_s'], 0)} |"]
    text = "\n".join(lines) + "\n"
    (out / "summary.md").write_text(text, encoding="utf-8")
    return text


def _med1(xs: list[float]) -> str:
    return f"{statistics.median(xs):.1f}" if xs else "-"


def _row(label: str, a: list[float], b: list[float], digits: int = 0) -> str:
    return f"| {label} | {_span(a, digits)} | {_span(b, digits)} |"


def compare(first: str, later: str) -> str:
    """The two rounds side by side, for docs/validation/load_compare.md."""
    x, y = collect(first), collect(later)
    lines = ["| Measure | First version | Later version |", "|---|---|---|"]
    for name in x["per_name"]:
        if name == "TOTAL" or name.startswith("MQTT connect") or name not in y["per_name"]:
            continue
        p, q = x["per_name"][name], y["per_name"][name]
        lines += [_row(f"{name}: per second", p["rps"], q["rps"], 1), _row(f"{name}: p50 (ms)", p["p50"], q["p50"]),
                  _row(f"{name}: p95 (ms)", p["p95"], q["p95"]), _row(f"{name}: failures (%)", p["fail_pct"], q["fail_pct"], 2)]
    for kind in ("segment", "state"):
        a, b = x["lag"][kind], y["lag"][kind]
        lines += [_row(f"Until the API shows a {kind} change: p50 (ms)", a["p50"], b["p50"]),
                  _row(f"Until the API shows a {kind} change: p95 (ms)", a["p95"], b["p95"]),
                  _row(f"Until the API shows a {kind} change: slowest (ms)", a["max"], b["max"]),
                  _row(f"{kind} probes that timed out (of {_span(a['n'], 0)} / {_span(b['n'], 0)})", a["timeouts"], b["timeouts"])]
    lines += _row("PostgreSQL: tuples inserted + updated per second", x["pg"]["writes_per_s"], y["pg"]["writes_per_s"]),
    lines += [_row("PostgreSQL: committed transactions per second", x["pg"]["commits_per_s"], y["pg"]["commits_per_s"])]
    lines += [_row("Whole host: mean CPU (% of all logical processors)", x["host_mean"], y["host_mean"])]
    return "\n".join(lines) + "\n"


def compare_containers(first: str, later: str) -> str:
    x, y = collect(first), collect(later)
    names = sorted(set(x["cpu"]) | set(y["cpu"]))
    lines = ["| Container | CPUs used, first | CPUs used, later | Memory MiB (highest), first | Memory MiB, later |",
             "|---|---|---|---|---|"]
    for n in names:
        lines.append(f"| {n.replace('camera-cloud-core-', '').rsplit('-1', 1)[0]} | {_span(x['cpu'].get(n, []), 2)} | "
                     f"{_span(y['cpu'].get(n, []), 2)} | {_span(x['mem'].get(n, []), 0)} | {_span(y['mem'].get(n, []), 0)} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["run", "summarize", "compare", "clean"])
    ap.add_argument("--label", default="v1")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--seconds", type=int, default=340)
    ap.add_argument("--files", nargs="+", default=FILES, help="compose files, in order")
    a = ap.parse_args()
    set_files(a.files)
    if a.action == "run":
        run(a.label, a.runs, a.seconds)
    elif a.action == "summarize":
        sys.stdout.reconfigure(encoding="utf-8")
        print(summarize(a.label))
    elif a.action == "compare":
        sys.stdout.reconfigure(encoding="utf-8")
        print(compare("v1", a.label))
        print(compare_containers("v1", a.label))
    else:
        compose("exec", "-T", "locust", "python", "/app/setup_load.py", "--remove", check=False)
        drop_load_data()
