import re

def process_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    def replacer(match):
        inner = match.group(1)
        parts = inner.split(',')
        new_parts = []
        for p in parts:
            if ':' in p:
                k, v = p.split(':', 1)
                k = k.strip()
                v = v.strip()
                new_parts.append(f'{{"id": {k}, "value": {v}}}')
            else:
                new_parts.append(p)
        return '"fields": [' + ", ".join(new_parts) + ']'

    new_content = re.sub(r'"fields":\s*\{([^{}]*)\}', replacer, content)

    if new_content != content:
        with open(filepath, 'w') as f:
            f.write(new_content)
        print(f"Updated {filepath}")

process_file('scripts/scripted_provider.py')
process_file('eval/runner.py')
