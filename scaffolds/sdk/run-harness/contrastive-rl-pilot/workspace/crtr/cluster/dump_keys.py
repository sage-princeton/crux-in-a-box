import torch, json, sys
sd = torch.load(sys.argv[1], map_location="cpu")
ks = list(sd.keys())
print("n keys", len(ks))
print("first 8:", ks[:8])
print("A-like:", [k for k in ks if k in ("A", "Q", "log_lambda")])
json.dump(ks, open("/tmp/port_keys.json", "w"))
