jq:
  pkg.installed: []

python3:
  pkg.installed: []

probe:
  file.exists:
    - name: {{ pillar["bencher"]["artifacts"]["probe"] }}
