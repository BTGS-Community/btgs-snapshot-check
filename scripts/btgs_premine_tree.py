#!/usr/bin/env python3
"""BTGS premine tree (read-only). Shows where the block-1 premine went, hop by hop."""
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
    ap.add_argument("--depth", type=int, default=4, help="how many hops to show")
    ap.add_argument("--max-outputs", type=int, default=15, help="outputs shown per tx")
    ap.add_argument("--height", type=int, default=0, help="stop at this block height (default: chain tip)")
    a = ap.parse_args()
    rpc = RPC(a.port, a.cookie or os.path.join(a.datadir, ".cookie"))
    tip = a.height or rpc("getblockchaininfo")["blocks"]
    print(f"Walking {tip} blocks (read-only)...")

    out = {}          # (txid,n) -> (value, owner)
    spent_by = {}     # (txid,n) -> txid
    tx = {}           # txid -> dict(h, time, ins=[keys], outs=[n])
    unspent = set()
    premine = None
    t0 = time.time()
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
        if h % 2000 == 0 or h == tip:
            print(f"  block {h}/{tip} ({time.time()-t0:.0f}s)", flush=True)

    balance = defaultdict(Decimal)
    for k in unspent:
        v, o = out[k]; balance[o] += v

    L = []; w = L.append
    shown = set()
    first_hop_addrs = defaultdict(Decimal)

    def date(ts):
        return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")

    def walk(key, depth, indent):
        v, o = out[key]
        pad = "  " * indent
        if key in unspent:
            w(f"{pad}{fmt(v):>18s}  {o}   [UNSPENT]  (address balance now {fmt(balance[o])})")
            return
        nxt = spent_by[key]; ti = tx[nxt]
        w(f"{pad}{fmt(v):>18s}  {o}   -> spent at block {ti['h']} ({date(ti['time'])}) in {nxt[:16]}…")
        if depth >= a.depth:
            return
        if nxt in shown:
            w(f"{pad}    (tx {nxt[:16]}… already shown above)")
            return
        shown.add(nxt)
        ins_total = sum(out[k][0] for k in ti["ins"])
        other = ins_total - v
        outs = sorted(((out[(nxt, n)][0], n) for n in ti["outs"]), reverse=True)
        outs_total = sum(x for x, _ in outs)
        w(f"{pad}    TX {nxt[:16]}…  inputs {fmt(ins_total)} ({len(ti['ins'])} in"
          + (f", {fmt(other)} from OTHER sources" if other > 0 else "") + f")  -> {len(outs)} outputs, fee {fmt(ins_total-outs_total)}")
        for i, (val, n) in enumerate(outs):
            if i >= a.max_outputs:
                rest = outs[a.max_outputs:]
                w(f"{pad}      … {len(rest)} more outputs totalling {fmt(sum(x for x, _ in rest))}")
                break
            if depth == 0:
                first_hop_addrs[out[(nxt, n)][1]] += val
            walk((nxt, n), depth + 1, indent + 2)

    w("BTGS PREMINE TREE (read-only)")
    w("=" * 70)
    w(f"Chain tip: {tip}.  Showing {a.depth} hops.  Amounts in BTGS.")
    w("")
    for key in premine:
        walk(key, 0, 0)
    w("")
    w("FIRST-HOP RECIPIENTS (who got coins directly from the premine)")
    w("-" * 70)
    for o, v in sorted(first_hop_addrs.items(), key=lambda x: -x[1]):
        w(f"  {o:62s} received {fmt(v):>16s}   balance now {fmt(balance[o]):>16s}")
    report = "\n".join(L)
    with open("btgs-premine-tree.txt", "w") as f:
        f.write(report + "\n")
    print("\n" + report + "\n\nSaved: btgs-premine-tree.txt")

if __name__ == "__main__":
    main()
