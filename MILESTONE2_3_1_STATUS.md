# Milestone 2.3.1 — Auto-Reflow Hotfix

Perbaikan utama:

- Auto-Reflow sekarang dipicu untuk Jalan, Kavling, RTH, dan PSU.
- Memindahkan RTH/PSU akan memaksa kavling di area terdampak untuk direflow terhadap jalan terdekat.
- RTH dan PSU diperlakukan sebagai obstacle pada validasi dan solver.
- Saat jalan digeser, kavling yang terhubung ikut bergerak sebagai live preview, lalu dirapikan solver setelah mouse dilepas.
- Validasi baru mendeteksi overlap kavling dengan RTH/PSU dan overlap RTH dengan PSU.
- Solver menjalankan dua pass collision resolution dan obstacle resolution.
- Jika ruang benar-benar tidak cukup, solver melepas minimum kavling konflik sebagai last resort agar siteplan tidak dibiarkan overlap.
- Tombol Reflow Local kini aktif juga ketika RTH atau PSU dipilih.

Smoke test sample Pekanbaru:

- Base layout: valid
- Road moved: final overlap 0, outside 0, missing frontage 0
- Lot moved into neighbour: final overlap 0, outside 0, missing frontage 0
- RTH moved into lots: final overlap lots-vs-RTH/PSU 0; unresolved lots are dropped only as a last resort.
