# Tasks — btc-bot (Kiro Spec)

> Implementasi inkremental, test-first. Jangan loncat fase. Ref: /docs/10-ROADMAP.md

## Fase 0 — Scaffolding & Read-only
- [x] 1. Setup repo, tooling (uv/poetry, ruff, mypy, pytest, pre-commit, CI). _(Req 9)_
- [x] 2. Settings (pydantic), MODE gating, .env.example, .gitignore. _(Req 9, 7)_
- [x] 3. adapters/clock.py (System+Sim) + test. _(NFR determinisme)_
- [x] 4. adapters/gamma.py: discovery round BTC 5m + test mock. _(Req 1)_
- [x] 5. adapters/clob_ws.py: stream market, reconnect, stale + test. _(Req 1)_
- [x] 6. adapters/chainlink.py: price_now/start_price + test. _(Req 1,2)_
- [x] 7. data/store.py + recorder.py: persist rounds/book/signals + test. _(Req 1,8)_
- [x] 8. app/cli.py: boot sequence + runner readonly (NO orders). _(Req 1)_

## Fase 1 — Backtest
- [ ] 9. domain/market.py (interval-loader) + test. _(Req 2,3)_
- [ ] 10. domain/signal.py (p_win, net_edge) + test kalibrasi. _(Req 2)_
- [ ] 11. domain/strategy.py (entry/hedge/exit, never-fade) + test. _(Req 3)_
- [x] 12. exec/sizing.py (fractional Kelly+caps) + property test. _(Req 6)_ — Phase 0.7
- [ ] 13. backtest/replay.py + fill model (slippage/latensi/kompetisi). _(Req 4)_
- [ ] 14. Laporan metrik+kalibrasi+sensitivitas+ablation. _(Req 4)_

## Fase 2 — Paper Trading
- [ ] 15. risk/manager.py (veto, kill-switch, circuit breaker) + test. _(Req 6)_
- [ ] 16. exec/oms.py mode paper (sim fill realtime) + test. _(Req 5)_
- [ ] 17. app/paper.py: loop realtime, ledger PnL, equity_curve, log. _(Req 5,8)_
- [ ] 18. Reconciliation + alerting dasar. _(Req 8)_

## Fase 3 — Live Micro-stakes (gated)
- [ ] 19. Signer EIP-712 (CLOB V2) + auth REST + test. _(Req 7)_
- [ ] 20. oms.py mode live (idempotency, retry) + Risk wajib. _(Req 6,7)_
- [ ] 21. Limits konservatif + LIVE_CONFIRMED gate + kill/circuit teruji. _(Req 6,7)_
- [ ] 22. Monitoring (Prometheus/Grafana) + alert (Telegram/Discord). _(Req 8)_
- [ ] 23. app/live.py: $1–$5/ronde, daily-loss kecil. _(Req 7)_

## Fase 4 — Hardening & Scale
- [ ] 24. Failover RPC/WSS, deploy Docker+systemd, backup DB.
- [ ] 25. Tuning param dari data live + ADR.
- [ ] 26. Scale-up HANYA jika PnL live positif & stabil.



---

## Telegram Control Plane (cross-cutting — docs/12)
- [ ] T.1 adapters/telegram.py: Notifier (push, queue, mock test) + BotEvent bus. _(Req 10)_
- [ ] T.2 app/control.py ControlFacade + handler read-only (/status /pnl /positions
      /recent) + inline keyboard + whitelist. _(Req 10)_
- [ ] T.3 Aksi kontrol /pause /resume /kill: konfirmasi 2-langkah + RiskManager +
      audit log; uji Telegram-down tidak nonaktifkan kill-switch CLI. _(Req 6,10)_
- [ ] T.0 Setup: @BotFather token, chat_id whitelist, env Telegram. _(Req 10)_



---

## Strategi (docs/13) & Multi-Market (docs/14)
- [ ] S.1 Fair-value engine (#1) + kalibrasi. _(Req 11)_
- [ ] S.2 Delta-hedge arb (#2) — opsional. _(Req 11)_
- [ ] S.3 Market making (#3) — opsional. _(Req 11)_
- [ ] M.1 Market registry + MarketSpec config. _(Req 12)_
- [ ] M.2 MarketScanner + per-market Worker + PriceFeed per aset. _(Req 12)_
- [ ] M.3 Risk multi-market & korelasi. _(Req 6,12)_
- [ ] M.4 Rollout bertahap (validasi per market). _(Req 12)_
