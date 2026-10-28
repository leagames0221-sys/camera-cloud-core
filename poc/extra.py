import json, pathlib, sys
import httpx
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import make_keys as m
K = m.load()
T = "11111111-1111-4111-8111-111111111111"
P = T + "/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
h = {"Authorization": "Bearer " + m.token(K, "api", None, ttl=60)}
for name, url in {
    "paths_list_after_publisher_stopped": "/v3/paths/list",
    "recordings_get_known": f"/v3/recordings/get/{P}",
    "recordings_get_unknown": f"/v3/recordings/get/{T}/cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    "recordings_list": "/v3/recordings/list",
}.items():
    r = httpx.get("http://127.0.0.1:19997" + url, headers=h)
    print(name, r.status_code, r.text[:700].replace("\n", " "))
