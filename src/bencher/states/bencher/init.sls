bencher-bin:
  file.directory:
    - name: /var/lib/bencher/bin
    - makedirs: True

{% for name in ["telegraf", "victoria"] %}
bencher-{{ name }}-bin:
  file.copy:
    - name: /var/lib/bencher/bin/{{ name }}
    - source: /bencher/payload/{{ name }}
    - force: True
    - mode: "0755"
    - require:
      - file: bencher-bin

bencher-{{ name }}-unit:
  file.managed:
    - name: /etc/systemd/system/bencher-{{ name }}.service
    - source: salt://bencher/{{ name }}.service
{% endfor %}

bencher-telegraf-conf:
  file.managed:
    - name: /var/lib/bencher/telegraf.conf
    - source: salt://bencher/telegraf.conf.jinja
    - template: jinja

bencher-victoria:
  service.running:
    - name: bencher-victoria
    - enable: False
    - require:
      - file: bencher-victoria-bin
      - file: bencher-victoria-unit

bencher-victoria-health:
  http.wait_for_successful_query:
    - name: http://127.0.0.1:8428/health
    - status: 200
    - wait_for: 60
    - require:
      - service: bencher-victoria

bencher-telegraf:
  service.running:
    - name: bencher-telegraf
    - enable: False
    - require:
      - file: bencher-telegraf-bin
      - file: bencher-telegraf-unit
      - file: bencher-telegraf-conf
      - http: bencher-victoria-health

bencher-ready:
  cmd.run:
    - name: /bencher/payload/salt/bin/python3 -I /var/lib/bencher/bencher/ready.py
    - require:
      - service: bencher-telegraf
