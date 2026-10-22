#!/usr/bin/env python3
"""BTGS multisig check (read-only).
Finds all P2WSH (multisig-type) addresses that received a lot of BTGS, and for
every one that has ever SPENT coins it shows the script that was revealed:
how many signatures are needed (e.g. 2-of-2) and which public keys are in it,
plus the normal G... / bcg1q... addresses those keys belong to."""
import argparse, base64, json, os, time, urllib.request
from collections import defaultdict
from decimal import Decimal

class RPC:
    def __init__(self, port, cookie):
        auth = open(cookie).read().strip()
        self.url = f"http://127.0.0.1:{port}/"
        self.hdr = {"Authorization": "Basic " + base64.b64encode(auth.encode()).decode(),
                    "Content-Type": "application/json"}
        self.n = 0
    def __call__(self, method, *params):
        self.n += 1
        body = json.dumps({"jsonrpc": "1.0", "id": self.n, "method": method, "params": list(params)}).encode()
        with urllib.request.urlopen(urllib.request.Request(self.url, body, self.hdr), timeout=300) as r:
            res = json.loads(r.read(), parse_float=Decimal)
        if res.get("error"):
            raise RuntimeError(f"{method}: {res['error']}")
        return res["result"]

def fmt(x):
    return f"{x:,.8f}".rstrip("0").rstrip(".")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18881)
    ap.add_argument("--datadir", default=os.path.expanduser("~/.btgs"))
    ap.add_argument("--min", type=Decimal, default=Decimal(10000), help="only show P2WSH addresses that received at least this much")
    ap.add_argument("--height", type=int, default=0, help="stop at this block height (default: chain tip)")
    a = ap.parse_args()
    rpc = RPC(a.port, os.path.join(a.datadir, ".cookie"))
    tip = a.height or rpc("getblockchaininfo")["blocks"]
    print(f"Walking {tip} blocks (read-only)...")

    out = {}                                   # (txid,n) -> (value, address)
    received = defaultdict(Decimal)            # p2wsh address -> total received
    first_seen = {}
    balance = defaultdict(Decimal)
    spends = defaultdict(list)                 # p2wsh address -> [(height, txid, witness_script_hex, value)]
    t0 = time.time()
    for h in range(1, tip + 1):
        blk = rpc("getblock", rpc("getblockhash", h), 2)
        for t in blk["tx"]:
            for vin in t["vin"]:
                if "coinbase" in vin:
                    continue
                k = (vin["txid"], vin["vout"])
                if k in out:
                    v, ad = out.pop(k)
                    balance[ad] -= v
                    wit = vin.get("txinwitness") or []
                    if wit:
                        spends[ad].append((h, t["txid"], wit[-1], v))
            for vo in t["vout"]:
                spk = vo["scriptPubKey"]
                if spk.get("type") != "witness_v0_scripthash":
                    continue
                ad = spk.get("address", "p2wsh:" + spk.get("hex", "")[:20])
                v = Decimal(vo["value"])
                out[(t["txid"], vo["n"])] = (v, ad)
                received[ad] += v
                balance[ad] += v
                first_seen.setdefault(ad, h)
        if h % 4000 == 0 or h == tip:
            print(f"  block {h}/{tip} ({time.time()-t0:.0f}s)", flush=True)

    def key_addresses(pk):
        res = []
        for kind in ("pkh", "wpkh"):
            try:
                desc = rpc("getdescriptorinfo", f"{kind}({pk})")["descriptor"]
                res.append(rpc("deriveaddresses", desc)[0])
            except Exception:
                pass
        return res

    L = ["BTGS MULTISIG (P2WSH) CHECK (read-only)", "=" * 72,
         f"Chain tip {tip}. Showing P2WSH addresses that received at least {fmt(a.min)} BTGS.", ""]
    big = sorted((ad for ad in received if received[ad] >= a.min), key=lambda x: -received[x])
    if not big:
        L.append("None found.")
    for ad in big:
        L.append(ad)
        L.append(f"  first seen block {first_seen[ad]}   received {fmt(received[ad])}   balance now {fmt(balance[ad])}")
        if not spends[ad]:
            L.append("  never spent -> its script is NOT visible on chain yet (owners must show it)")
            L.append("")
            continue
        scripts = {}
        for h, txid, ws, v in spends[ad]:
            scripts.setdefault(ws, []).append((h, txid, v))
        for ws, uses in scripts.items():
            dec = rpc("decodescript", ws)
            asm = dec.get("asm", "")
            L.append(f"  spent {len(uses)} time(s), first at block {uses[0][0]} (tx {uses[0][1][:16]}...)")
            L.append(f"  script type: {dec.get('type')}")
            parts = asm.split()
            if dec.get("type") == "multisig" and len(parts) >= 4:
                m, n = parts[0], parts[-2]
                L.append(f"  >>> {m}-of-{n} multisig. Keys in it:")
                for pk in parts[1:-2]:
                    adrs = key_addresses(pk)
                    L.append(f"      key {pk[:20]}...  = {' / '.join(adrs) if adrs else '(could not derive address)'}")
            else:
                L.append(f"  script: {asm[:200]}")
        L.append("")
    report = "\n".join(L)
    open("btgs-multisig-check.txt", "w").write(report + "\n")
    print("\n" + report + "\nSaved: btgs-multisig-check.txt")

if __name__ == "__main__":
    main()
