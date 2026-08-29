from pathlib import Path

ROOT = Path(__file__).resolve().parent
app_js = (ROOT / 'web/app.js').read_text(encoding='utf-8')
index = (ROOT / 'web/index.html').read_text(encoding='utf-8')
monitor = (ROOT / 'web/recovery-monitor.js').read_text(encoding='utf-8')
recovery = (ROOT / 'app/recovery_solver.py').read_text(encoding='utf-8')
main = (ROOT / 'app/main.py').read_text(encoding='utf-8')

assert 'const DEVOS_FRONTEND_VERSION = "2.5.14";' in app_js
assert 'Milestone 2.5.14' in index
assert 'M2.5.14 — Recovery Solver Monitor' in index
assert '/static/app.css?v=2.5.14' in index
assert '/static/recovery-monitor.css?v=2.5.14' in index
assert '/static/app.js?v=2.5.14' in index
assert '/static/recovery-monitor.js?v=2.5.14' in index
assert '"version": "2.5.14"' in main
assert 'M2.5.13' not in monitor
assert 'M2.5.13' not in recovery
print('M2.5.14 VERSION SYNC TEST PASSED')
