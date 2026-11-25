"""Check both the current public Vercel schema and repository entrypoints."""

import json
import re
import sys
from pathlib import Path
from urllib.request import urlopen

import jsonschema

root = Path(__file__).resolve().parents[1]
config = json.loads((root / "vercel.json").read_text(encoding="utf-8"))
if len(sys.argv) > 1:
    schema = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
else:
    with urlopen(config["$schema"], timeout=15) as response:
        schema = json.load(response)
# Vercel uses newer JSON Schema keywords alongside its draft declaration.
jsonschema.Draft7Validator(schema).validate(config)

web, api = (config["services"][name] for name in ("web", "api"))
assert config["fluid"] is True
module, symbol = api["entrypoint"].split(":")
entrypoint = root / api["root"] / (module.replace(".", "/") + ".py")
assert entrypoint.is_file() and symbol == "app"
assert (root / api["root"] / "ml/model.json").is_file()
assert (root / web["root"] / "index.html").is_file()
assert (root / web["root"] / "package-lock.json").is_file()
assert (root / api["root"] / "uv.lock").is_file()
assert {r["source"] for r in web["rewrites"] if r["destination"] == "/index.html"} == {
    "/training/:path*", "/workspace",
}

for path, expected in {
    "/": "web", "/training/example/nurse": "web", "/assets/app.js": "web",
    "/api": "api", "/api/health": "api", "/api/missions/example/intake": "api",
    "/api/socket.io": "api", "/api/socket.io/": "api",
}.items():
    actual = next(r["destination"]["service"] for r in config["rewrites"] if re.fullmatch(r["source"], path))
    assert actual == expected, (path, expected, actual)
    print(f"{path} -> {actual}")
print("Vercel schema and service entrypoints validated.")
