from datetime import datetime, UTC

t = datetime(2026, 6, 26, 12, 52, tzinfo=UTC)
ts = int(t.timestamp())
aligned = (ts // 300) * 300

print(f"Clock: {t}")
print(f"Timestamp: {ts}")
print(f"Aligned (5m): {aligned}")
print(f"\nExpected slug epochs (next 6 windows):")
for i in range(6):
    e = aligned + (i*300)
    dt = datetime.fromtimestamp(e, tz=UTC)
    slug = f"btc-updown-5m-{e}"
    print(f"  {i}: {slug} ({dt})")

print(f"\nTest fixture slug: btc-updown-5m-1782478200")
print(f"Fixture epoch 1782478200 = {datetime.fromtimestamp(1782478200, tz=UTC)}")
