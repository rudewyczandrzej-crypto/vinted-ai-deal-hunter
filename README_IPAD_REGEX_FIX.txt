# iPad regex filter fix

Replace only main.py.

Fix:
- iPad 7th gen and iPad 8gen are explicitly rejected.
- Old iPad generations 1-8 are rejected.
- Allowed:
  iPad 9 gen / 9th / 2021
  iPad 10 gen / 10th / 2022
  iPad A16
  iPad 10.9 / 10,9
  iPad 11 / 2025
- Plain 10.2 no longer passes, because iPad 7/8 also have 10.2.

Test:
/debug ipad

Expected:
iPad 8gen -> product_filter False, old ipad generation rejected
