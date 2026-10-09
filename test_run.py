
import requests

res = requests.post('http://localhost:8000/run', json={
    'task': 'test task',
    'chaos': None,
    'auto_approve': True,
    'vendor_name': 'Acme Corp',
    'seed_profile': 'base',
    'reset_env': True
})
data = res.json()
run_id = data.get('run_id')
print(f"Run ID: {run_id}")

if run_id:
    # Use requests to stream the SSE
    s = requests.Session()
    resp = s.get(f'http://localhost:8000/stream/{run_id}', stream=True)
    for line in resp.iter_lines():
        if line:
            print(line.decode('utf-8'))
