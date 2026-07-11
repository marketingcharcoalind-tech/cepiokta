# Time-Based Latency Sensitivity Guide

Status: Fase 1 read-only, G1 masih BLOCKED. Tool ini tidak menulis database, tidak memanggil OMS, dan tidak membuat order.

## Tujuan

Menguji kandidat `t_entry=60`, `delta=50`, `min_price=0.96`, `max_price=0.99` dengan tujuh model latency independen: tick 0, tick 1, serta waktu 50/100/250/500/1000 ms.

Dataset di-stream satu kali per proses. Setiap konfigurasi memiliki bankroll sendiri, sehingga hasil satu konfigurasi tidak mencemari konfigurasi lain.

## Output

Per konfigurasi tool melaporkan ronde, signal attempts, fills, entries, win/loss, net PnL, ROI, final balance, counter no-future entry/hedge/exit, latency median/P95/max, overshoot median/P95, stale target-book rate dan age, entry UP/DOWN, serta loss rounds.

## Command analisis5.db

```bash
cd ~/cepiokta
source venv/bin/activate
python -m btcbot.backtest.time_latency_sensitivity \
  --db "sqlite+aiosqlite:///./analisis5.db" \
  --since "2026-07-04T14:00:00+00:00" \
  --until "2026-07-10T13:25:00+00:00" \
  --t-entry 60 \
  --delta-threshold 50 \
  --min-price 0.96 \
  --max-price 0.99 \
  --starting-balance 500 \
  --max-rounds 2000 \
  --csv time_latency_sensitivity_analisis5.csv
```

## Aturan keputusan

Jangan mengubah G1 menjadi LANJUT hanya karena satu baris positif. Edge harus tetap positif setelah fee dan slippage pada latency waktu realistis, stabil di split lama dan baru, serta tidak bergantung pada stale book atau fallback no-future. Orders dan fills database readonly wajib tetap nol.
