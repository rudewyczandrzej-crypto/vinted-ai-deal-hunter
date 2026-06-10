# iCloud/Apple ID + Redmi Pad Pro 2 filter fix

Replace only main.py.

Fix 1: iCloud / Apple ID filter
- No longer rejects normal text just because it contains "iCloud" or "Apple ID".
- Allows phrases like:
  iCloud wylogowany
  wylogowany z iCloud
  bez blokady iCloud
  Apple ID usunięte
  zresetowany
  gotowy do sparowania
- Rejects only bad context:
  blokada iCloud / Apple ID
  zablokowany iCloud / Apple ID
  activation lock
  iCloud lock
  brak hasła
  nie znam hasła

Fix 2: Redmi Pad Pro filter
- Now requires Redmi Pad Pro 2 / newer hints:
  redmi pad pro 2
  2 generacji
  2 gen
  2025
  Snapdragon 7s
  12.1 / 12,1
  HyperOS 2
- Rejects first generation:
  redmi pad pro 1
  1 generacji
  2024

Test:
/debug redmi pad pro
/debug apple watch se

Expected:
- Redmi Pad Pro 1 / 2024 -> product_filter False
- Redmi Pad Pro 2 / 2025 / 12.1 -> product_filter True
- "bez blokady iCloud" should not be rejected by quality filter
