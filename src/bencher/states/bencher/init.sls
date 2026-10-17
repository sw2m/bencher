bencher-reporters:
  file.directory:
    - name: /run/bencher/reporters
    - makedirs: True

bencher-telegraf-conf:
  file.managed:
    - name: /var/lib/bencher/telegraf.conf
    - source: salt://bencher/telegraf.conf.jinja
    - template: jinja

bencher-victoria:
  service.running:
    - name: victoria-metrics

bencher-victoria-health:
  http.wait_for_successful_query:
    - name: http://127.0.0.1:8428/health
    - status: 200
    - wait_for: 60
    - require:
      - service: bencher-victoria

bencher-telegraf:
  service.running:
    - name: telegraf
    - require:
      - file: bencher-reporters
      - file: bencher-telegraf-conf
      - http: bencher-victoria-health

bencher-ready:
  http.wait_for_successful_query:
    - name: http://127.0.0.1:8428/api/v1/export?match%5B%5D=%7Breporter%3D%22system%22%7D
    - match: __name__
    - wait_for: 60
    - require:
      - service: bencher-telegraf
