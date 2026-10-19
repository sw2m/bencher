jq:
  pkg.installed: []

python3:
  pkg.installed: []

{% if pillar["bencher"]["phase"] == "run" %}
probe:
  file.exists:
    - name: {{ pillar["bencher"]["artifacts"]["probe"] }}
{% endif %}

resolved:
  file.serialize:
    - name: /opt/ci/resolved.json
    - dataset: {{ pillar["bencher"]["resolved"] | json }}
    - serializer: json
    - makedirs: True
