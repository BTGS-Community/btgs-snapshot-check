#!/usr/bin/env python3
"""BTGS premine follower (read-only).
Follows the big premine lump transaction by transaction ("peel chain"),
lists every amount peeled off along the way, and follows big splits."""
import argparse, base64, json, os, time, urllib.request, datetime
from collections import defaultdict
from decimal import Decimal

class RPC:
    def __init__(self, port, cookie):
        with open(cookie) as f:
            auth = f.read().strip()
        self.url = f"http://127.0.0.1:{port}/"
        self.hdr = {"Authorization": "Basic " + base64.b64encode(auth.encode()).decode(),
                    "Content-Type": "application/json"}
        self.n = 0
    def __call__(self, method, *params):
        self.n += 1
        body = json.dumps({"jsonrpc": "1.0", "id": self.n, "method": method, "params": list(params)}).encode()
        req = urllib.request.Request(self.url, body, self.hdr)
        with urllib.request.urlopen(req, timeout=300) as r:
            res = json.loads(r.read(), parse_float=Decimal)
        if res.get("error"):
            raise RuntimeError(f"{method}: {res['error']}")
        return res["result"]

def owner(spk):
    if spk.get("address"):
        return spk["address"]
    if spk.get("type") == "pubkey":
        return "pubkey:" + spk["asm"].split()[0]
    return f"{spk.get('type','?')}:{spk.get('hex','')[:40]}"

def fmt(x):
    return f"{x:,.8f}".rstrip("0").rstrip(".")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18881)
    ap.add_argument("--datadir", default=os.path.expanduser("~/.btgs"))
    ap.add_argument("--cookie")
    ap.add_argument("--branch-min", type=Decimal, default=Decimal(50000),
                    help="also follow any output at least this big as its own branch")
    ap.add_argument("--height", type=int, default=0, help="stop at this block height (default: chain tip)")
    a = ap.parse_args()
    rpc = RPC(a.port, a.cookie or os.path.join(a.datadir, ".cookie"))
    tip = a.height or rpc("getblockchaininfo")["blocks"]
    print(f"Walking {tip} blocks (read-only)...")
    out = {}; spent_by = {}; tx = {}; unspent = set(); premine = None; t0 = time.time()
    for h in range(1, tip + 1):
        blk = rpc("getblock", rpc("getblockhash", h), 2)
        for t in blk["tx"]:
            txid = t["txid"]; ins = []
            for vin in t["vin"]:
                if "coinbase" in vin: continue
                k = (vin["txid"], vin["vout"]); ins.append(k)
                spent_by[k] = txid; unspent.discard(k)
            outs = []
            for vo in t["vout"]:
                spk = vo["scriptPubKey"]
                if spk.get("type") == "nulldata": continue
                k = (txid, vo["n"]); out[k] = (Decimal(vo["value"]), owner(spk))
                unspent.add(k); outs.append(vo["n"])
            tx[txid] = {"h": h, "time": blk["time"], "ins": ins, "outs": outs}
            if h == 1 and "coinbase" in t["vin"][0]:
                premine = [(txid, n) for n in outs]
        if h % 4000 == 0 or h == tip:
            print(f"  block {h}/{tip} ({time.time()-t0:.0f}s)", flush=True)
    balance = defaultdict(Decimal)
    for k in unspent:
        v, o = out[k]; balance[o] += v

    def date(ts):
        return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d")

    L = []; w = L.append
    peels = defaultdict(Decimal)          # recipient -> total peeled to it
    mixed_in = Decimal(0)
    seen_tx = set()
    branches = [(k, "premine") for k in premine]
    endpoints = []

    while branches:
        key, label = branches.pop(0)
        w(f"\n=== Branch: {label}  start {fmt(out[key][0])} BTGS at {out[key][1]}")
        steps = 0
        while True:
            v, o = out[key]
            if key in unspent:
                w(f"  END: {fmt(v)} BTGS still UNSPENT at {o} (address balance now {fmt(balance[o])})")
                endpoints.append((o, v)); break
            nxt = spent_by[key]
            if nxt in seen_tx:
                w(f"  joins tx {nxt[:16]}... already followed"); break
            seen_tx.add(nxt); ti = tx[nxt]; steps += 1
            ins_total = sum(out[k][0] for k in ti["ins"])
            other = ins_total - v
            mixed_in += max(other, Decimal(0))
            outs = sorted(((out[(nxt, n)][0], n) for n in ti["outs"]), reverse=True)
            main_v, main_n = outs[0]
            side = outs[1:]
            big_side = [(x, n) for x, n in side if x >= a.branch_min]
            note = f"  block {ti['h']:>6} {date(ti['time'])}  in {fmt(ins_total):>18s}"
            if other > 0: note += f" (+{fmt(other)} other)"
            note += f"  -> keep {fmt(main_v):>18s}"
            if side:
                note += f" | sent out {len(side)}: " + ", ".join(
                    f"{fmt(x)}->{out[(nxt, n)][1][:14]}..." for x, n in side[:4])
                if len(side) > 4: note += f" (+{len(side)-4} more)"
            w(note)
            for x, n in side:
                rcpt = out[(nxt, n)][1]
                if x >= a.branch_min:
                    branches.append(((nxt, n), f"split at block {ti['h']} -> {rcpt}"))
                else:
                    peels[rcpt] += x
            if main_v < ins_total * Decimal("0.2") and len(outs) > 5:
                w(f"  BIG SPLIT into {len(outs)} outputs - remaining outputs counted as recipients")
                peels[out[(nxt, main_n)][1]] += main_v
                break
            key = (nxt, main_n)

    w("\n=== WHERE THE PEELED-OFF COINS WENT (amounts under the branch threshold)")
    w(f"Total peeled to {len(peels)} recipients: {fmt(sum(peels.values(), Decimal(0)))} BTGS")
    w(f"Coins from OTHER sources mixed in along the way: {fmt(mixed_in)} BTGS")
    for o, v in sorted(peels.items(), key=lambda x: -x[1])[:60]:
        w(f"  {o:62s} got {fmt(v):>16s}   balance now {fmt(balance[o]):>16s}")
    w("\n=== BRANCH ENDPOINTS (big lumps still unspent)")
    for o, v in sorted(endpoints, key=lambda x: -x[1]):
        w(f"  {o:62s} {fmt(v):>18s}")
    report = "\n".join(L)
    with open("btgs-premine-follow.txt", "w") as f:
        f.write(report + "\n")
    print(report + "\n\nSaved: btgs-premine-follow.txt")

if __name__ == "__main__":
    main()
