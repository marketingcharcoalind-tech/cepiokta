# Test fixtures

## `gamma_btc5m_markets.json`

Sample respons Gamma `/markets` (array market) untuk **snapshot schema** test
parser `adapters/gamma.py`. Berisi 4 market:

1. **BTC Up/Down 5m, OPEN** — harus lolos filter & ter-parse (status OPEN).
2. **BTC Up/Down 5m, RESOLVED** — outcomePrices `["1","0"]` → outcome UP.
3. **ETH Up/Down 1 jam** — ditolak filter (durasi ≠ 5m).
4. **BTC Yes/No (long-term)** — ditolak filter (outcomes bukan Up/Down).

> ⚠️ **Disusun dari skema yang DIDOKUMENTASIKAN** Gamma Markets API (camelCase,
> array ber-JSON pada `outcomes`/`clobTokenIds`/`outcomePrices`). Lingkungan
> dev saat ini berada di balik proxy TLS-intercepting sehingga capture respons
> live belum dapat dilakukan. **TODO (B3):** ganti dengan capture respons live
> sekali (mis. `scripts/read_chainlink_price.py` versi Gamma) agar regresi
> schema terkunci 1:1 dengan produksi.
