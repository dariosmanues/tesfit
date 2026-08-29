# Milestone 2.4.1 — Reflow Routing & Cache Hotfix

Fix utama:
- `/editor/reflow` dan `/editor/repack-block` sekarang menjadi compatibility bridge ke M2.4 Parametric Constraint Solver.
- Browser cache-busting untuk `app.js` dan `app.css` dengan `?v=2.4.1`.
- `/health` mengembalikan `version: 2.4.1`.
- Frontend fetch memakai `cache: no-store`.
- Launcher menampilkan M2.4.1.

Jika log masih menunjukkan POST `/editor/reflow`, backend tetap menjalankan parametric solver, bukan solver M2.3 lama.
