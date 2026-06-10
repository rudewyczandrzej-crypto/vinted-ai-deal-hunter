# Apple Watch SE 2 filter patch

Replace only main.py.

Fix:
- /add apple watch se will now pass only listings that clearly mention SE 2 / 2 generacji / 2. generacji / 2022 / 2023 / 2nd gen.
- Plain Apple Watch SE 40mm without "2 gen" will be rejected.
- The filter checks title + description + brand + raw Vinted text.

Test:
/debug apple watch se

Expected:
- Apple Watch SE 2 generacji -> product_filter True
- Apple Watch SE 40mm without 2 gen -> product_filter False, reason: apple watch is not clearly SE 2
