# Development OS — Milestone 2.5.4

## Optional Land Optimization — Release Candidate

### Perubahan utama
- Checkbox **Optimalisasi Lahan (M2.5)** tetap default OFF.
- OFF = skenario generate awal; ON -> OFF mengembalikan baseline generate.
- ON menambahkan adaptive residual frontage fill yang benar-benar membentuk polygon kavling dari residual road-fronting.
- Tidak ada residual-to-Reserve relabeling. `reserve_area_m2` harus 0 pada hasil optimizer.
- Hard target TRUE residual <= 3% total luas lahan.
- Jika adaptive packing/road search belum cukup, excess residual hanya boleh menjadi **RTH tambahan fungsional** dan dibatasi maksimum +5% total lahan; jika masih tidak cukup, optimizer FAIL eksplisit.
- Bounded optimizer dengan time budget (`max_optimize_seconds`) untuk mencegah pencarian menggantung.
- Projection-induced polygon invalidity diperbaiki sebelum GeoJSON output.

## Smoke test release
Semua kasus di bawah diuji dengan validasi polygon, overlap, containment, dan konflik terhadap jalan/RTH/PSU.

| Kasus | Baseline residual | Hasil residual | Kavling awal -> akhir | Waktu | Overlap | Outside | Reserve |
|---|---:|---:|---:|---:|---:|---:|---:|
| Sample Pekanbaru | 14.90% | **1.96%** | 105 -> 145 | 3.34 s | 0 m2 | 0 m2 | 0 m2 |
| Rectangle 220 x 120 m | 17.64% | **0.73%** | 93 -> 152 | 1.47 s | 0 m2 | 0 m2 | 0 m2 |
| Large 360 x 220 m | 15.38% | **0.64%** | 330 -> 412 | 6.39 s | 0 m2 | 0 m2 | 0 m2 |

Tambahan gate:
- `/health` -> version `2.5.4`: PASS.
- Python compile: PASS.
- Frontend `node --check`: PASS.
- M1 geometry smoke: PASS.
- M2.4 parametric smoke: PASS.
- Optional toggle baseline OFF: PASS.
- Optimizer full sample: PASS.
- Konfigurasi ekstrem yang hanya bisa mencapai 3% dengan RTH berlebihan: **ditolak 422**, bukan disamarkan sebagai RTH/Reserve.

### Release rule
Saat checkbox OFF, residual >3% boleh tetap tampil karena itu skenario awal. Saat checkbox ON, hasil hanya dianggap valid/savable jika TRUE residual <=3%.
