"""RPC login for the BTGS check scripts (read-only).
Order: 1) BTGS_RPCUSER + BTGS_RPCPASSWORD environment variables,
       2) the node's .cookie file,
       3) rpcuser / rpcpassword in btgs.conf (same folder as the cookie).
Nothing is sent anywhere except to your own node on 127.0.0.1."""
import os

def get_auth(cookie_path):
    user, pw = os.environ.get("BTGS_RPCUSER"), os.environ.get("BTGS_RPCPASSWORD")
    if user and pw:
        return f"{user}:{pw}"
    if os.path.isfile(cookie_path):
        with open(cookie_path) as f:
            return f.read().strip()
    conf = os.path.join(os.path.dirname(cookie_path), "btgs.conf")
    if os.path.isfile(conf):
        vals = {}
        with open(conf) as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if "=" in line:
                    k, v = line.split("=", 1)
                    vals.setdefault(k.strip(), v.strip())
        if vals.get("rpcuser") and vals.get("rpcpassword"):
            return f"{vals['rpcuser']}:{vals['rpcpassword']}"
    raise SystemExit(
        f"No RPC login found. Looked for {cookie_path} and rpcuser/rpcpassword in {conf}.\n"
        "Use --datadir, or set BTGS_RPCUSER and BTGS_RPCPASSWORD.")
