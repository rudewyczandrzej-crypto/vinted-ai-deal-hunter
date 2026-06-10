# iPad filter fix

Replace only main.py.

Fix:
- /add ipad now rejects iPad 7th gen, iPad 8gen and older/random iPads.
- It allows only:
  iPad 9 / 9 gen / 9th / 2021
  iPad 10 / 10 gen / 10th / 2022
  iPad A16 / 10.9 / 10,9 / iPad 11 / 2025
- It checks title + description + brand + raw Vinted text.

Test:
/debug ipad

Expected:
- iPad 7th gen -> product_filter False
- iPad 8gen -> product_filter False
- iPad 9 gen / 10 gen / 2022 / A16 -> product_filter True
