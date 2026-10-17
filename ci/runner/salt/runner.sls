jq:
  pkg.installed: []

probe:
  file.exists:
    - name: {{ pillar["bencher"]["artifacts"]["probe"] }}
