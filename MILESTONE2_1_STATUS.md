# Development OS — Milestone 2.1

## Manual Siteplan Adjustment

Fitur ini menambahkan tahap manual setelah generative site planning Milestone 2.

### Workflow
1. Input/draw/import batas lahan.
2. Generate alternatif siteplan otomatis.
3. Pilih salah satu alternatif.
4. Klik **Aktifkan Edit Manual**.
5. Klik objek pada peta lalu drag untuk memindahkan:
   - kavling,
   - jaringan jalan,
   - RTH,
   - PSU.
6. Gunakan nudge untuk adjustment presisi dalam meter.
7. Gunakan rotasi +/- 5 derajat.
8. Untuk kavling: duplicate atau delete.
9. Undo / Reset Generate kapan saja.
10. Klik **Hitung Ulang** atau simpan opsi terpilih.

### Validasi otomatis
Endpoint `/site-plan/recalculate` menghitung ulang:
- jumlah dan luas total kavling,
- efisiensi kavling,
- luas/persentase jalan,
- luas/persentase RTH,
- luas/persentase PSU,
- panjang drainase,
- sisa area,
- jumlah kavling di luar buildable area,
- pasangan kavling yang overlap.

### Keyboard
- Arrow keys: geser objek terpilih.
- Delete / Backspace: hapus kavling terpilih.
- Ctrl+Z: undo.

### Batasan v2.1
- Jalan masih diedit sebagai satu jaringan polygon, belum per-segmen/vertex.
- Belum ada snapping antar-kavling atau snapping frontage ke jalan.
- Belum ada resize kavling dengan drag handle.
- Drainase ikut berpindah/berotasi saat jaringan jalan dipindahkan/dirotasi.
- Layout tetap bersifat konseptual sampai aturan teknis dan DED dimasukkan.
