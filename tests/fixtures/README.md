# Test fixtures

## `gamma_updown_live_fixture.json`

Sample respons Gamma `/markets` (array market) untuk **snapshot schema** test
parser `adapters/gamma.py`. Berisi 4 market:

1. **btc-updown-5m**, OPEN, window 11:55→12:00 (in-window/next-round test).
2. **btc-updown-5m** (epoch +300), OPEN, window 12:00→12:05.
3. **eth-updown-15m**, OPEN (uji filter asset/timeframe).
4. **Yes/No long-dated** (`will-bitcoin-reach-200k-2026`) — ditolak (bukan slug up/down).

Karakteristik penting yang diuji (temuan data live, diverifikasi VPS):
- Identifikasi via **slug** regex `^(asset)-updown-(5m|15m)-(epoch)$`, bukan teks judul.
- `epoch` = waktu resolusi = `endDate` = `window_end`; sanity kelipatan 300/900.
- `window_start` dari `eventStartTime` — **bukan** `startDate` (tanggal listing
  ~24 jam sebelumnya; `startDate` sengaja diisi 25 Jun untuk membuktikan tidak dipakai).
- `clobTokenIds` sejajar index dengan `outcomes` (Up/Down tidak tertukar).
- `feeSchedule {exponent, rate, takerOnly, rebateRate}`, `feesEnabled`, `feeType`.
- `resolutionSource` = Chainlink **Data Streams**.

> ⚠️ **CATATAN KEJUJURAN:** file capture live yang dijanjikan
> (`tests/fixtures/gamma_updown_live_fixture.json`) tiba dalam keadaan **kosong
> (`[]`)** saat `git pull`. Record di sini **direkonstruksi presisi dari temuan
> data live yang diverifikasi di VPS** (tercantum di task B3). **TODO:** ganti
> dengan capture mentah 1:1 dari Gamma saat akses jaringan non-proxy tersedia,
> agar regresi schema benar-benar terkunci ke respons produksi.
