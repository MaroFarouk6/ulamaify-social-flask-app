import os

bind = "0.0.0.0:8000"
workers = int(os.getenv("WEB_WORKERS", "2"))
worker_class = "sync"
timeout = 120
graceful_timeout = 30
max_requests = 1000
max_requests_jitter = 100
accesslog = "-"
# Exclude query strings: authorization codes and sensitive inputs do not belong in access logs.
access_log_format = "%(h)s %(m)s %(U)s %(s)s %(L)s"
errorlog = "-"
capture_output = True
# Only ProxyFix consumes forwarded protocol/IP, when explicitly enabled.
forwarded_allow_ips = ""
