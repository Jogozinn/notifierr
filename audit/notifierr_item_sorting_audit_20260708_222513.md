# Notifierr Item-Sorting QA Audit

## 1. Executive Summary

- Time window: generated 2026-07-08T22:25:13.871482+00:00 from stored DB data only.
- Scan cycles reviewed: 10, 7.
- Total active items reviewed: 10.
- Needs Data items reviewed: 139 (all non-ignored including stale; active Needs Data count 8; broader non-ignored Needs Data count 139).
- Truly Needs Data / not enough info: 79.
- Could be auto-sorted: 44.
- Possible missed gems: 1.
- Possible false positives: 1.
- Biggest repeated issue: ('parts cost missing/pricing issue', 136).
- Full table: `audit\notifierr_needs_data_review_20260708_222513.csv`. JSON detail: `audit\notifierr_item_sorting_audit_20260708_222513.json`.

## 2. Needs Data Queue Breakdown

Active bucket counts:
- Needs Data / Needs Review: 4
- Rejected / Avoid: 3
- Priority Review: 2
- Best Pick / Candidate: 1

Needs Data reason counts:
- parts cost missing/pricing issue: 136
- description missing: 123
- specific repair issue missing: 76
- repair issue missing: 75
- resale missing: 70
- storage unknown: 33
- hard risk ambiguity: 31
- pricing confidence issue: 29
- accessory/component ambiguity: 13
- carrier unknown: 7

QA verdict counts:
- not_enough_info_even_after_description: 40
- should_remain_needs_data: 39
- should_be_priority_review: 27
- should_be_watch: 12
- app_missing_pricing_data: 12
- should_be_rejected: 4
- correctly_sorted: 4
- should_be_best_pick: 1

## 3. Needs Data Item-by-Item QA Table

Full table written to `audit\notifierr_needs_data_review_20260708_222513.csv`. Top 25 highest-priority examples:

|item_id|title|total_cost|current_bucket|manual_review_reason|key_description_evidence|qa_verdict|better_bucket|confidence|suggested_signal_rule|
|---|---|---|---|---|---|---|---|---|---|
|v1\|358606746046\|0|Broken Unlocked Apple iPhone 13 128GB 26.3 ML933LL/A Bad Battery|179.99|Needs Data / Needs Review|Expected profit below threshold; Only upside case works; Conservative profit is negative; Low-confidence pricing needs stronger profit; Profit depends on mint resale; ...|Broken Unlocked Apple iPhone 13 128GB 26.3 ML933LL/A Bad Battery Broken Unlocked Apple iPhone 13 128GB 26.3 ML933LL/A Bad Battery Items included in this sale: Broken U...|should_be_priority_review|Priority Review|medium|Use whole-phone evidence phrases: powers on, clean imei, unlocked, for parts, bad battery|
|v1\|157916351415\|0|Broken Unlocked Apple iPhone 13 Pro 256GB 26.2.1 MLTW3LL/A Bad Battery|299.99|Needs Data / Needs Review|Expected profit below threshold; Only upside case works; Low-confidence pricing needs stronger profit; Profit depends on mint resale; Parts estimate not verified; Low-...|Broken Unlocked Apple iPhone 13 Pro 256GB 26.2.1 MLTW3LL/A Bad Battery Broken Unlocked Apple iPhone 13 Pro 256GB 26.2.1 MLTW3LL/A Bad Battery Items included in this sa...|should_be_priority_review|Priority Review|medium|Use whole-phone evidence phrases: powers on, works, clean imei, unlocked, face id works, for parts, back glass, bad battery|
|v1\|327253131790\|0|Apple iPhone 14 oem cracked screen parts Read bad OLED|40.0|Best Pick / Candidate|Best Offer available|For one used Apple iPhone 14 original OLED screen, pulled from a working phone fully tested and working. The front has a lot of scratches, touch works, OLED ( line rig...|should_be_rejected|Rejected / Avoid|high|Auto-sort using phrase/flag evidence: works, phone is not included, for flex parts only, for parts, as is, no returns, cracked screen, bad oled|
|v1\|147334281535\|0|Apple iPhone 13 Pro 128GB A2483 Sierra Blue (Unlocked) - Back Glass Cracked|199.95|Needs Data / Needs Review|Conservative profit is negative; Low-confidence pricing needs stronger profit; Profit depends on mint resale; Model/spec mismatch; Parts estimate not verified; Low-con...|Home Add to favorites Feedback Contact Tablets Smartwatches Game Consoles Laptops & Computers Accessories Cell Phones Networking Computer Components Monitors Apple iPh...|should_be_best_pick|Best Pick / Candidate|medium|Use whole-phone evidence phrases: unlocked, no icloud, for parts, back glass|
|v1\|397971120585\|0|Broken Unlocked Apple iPhone 14 Pro 128GB 26.3.1 MPXT3LL/A Weak Battery Bad LCD|307.99|Needs Data / Needs Review|Expected profit below threshold; Profit depends on mint resale; Parts estimate not verified|Broken Unlocked Apple iPhone 14 Pro 128GB 26.3.1 MPXT3LL/A Weak Battery Bad LCD Broken Unlocked Apple iPhone 14 Pro 128GB 26.3.1 MPXT3LL/A Weak Battery Bad LCD Items i...|should_be_priority_review|Priority Review|medium|Use whole-phone evidence phrases: powers on, clean imei, unlocked, for parts, back glass|
|v1\|287359635450\|0|iPhone 15 128gb Unlocked, Cracked Back, Bad OLED, Swollen Battery, No IC READ!!|162.49|Needs Data / Needs Review|Expected profit below threshold; Only upside case works; Low-confidence pricing needs stronger profit; Profit depends on mint resale; Parts estimate not verified; Low-...|Read!! I am a reseller! I do not know the history of any IMEI. DO NOT PURCHASE IF YOU DONT UNDERSTAND WHAT THIS MEANS! ALL IMEIS ARE CLEAN AT TIME OF LISTING UNLESS OT...|should_be_priority_review|Priority Review|medium|Use whole-phone evidence phrases: powers on, unlocked, for parts, as is, bad oled, cracked back|
|v1\|800086544988\|0|Broken Unlocked Apple iPhone 15 Plus eSIM 128GB 26.0 MTXR3LL/A Cracked Back|299.99|Needs Data / Needs Review|Expected profit below threshold; Only upside case works; Low-confidence pricing needs stronger profit; Profit depends on mint resale; Parts estimate not verified; Low-...|Broken Unlocked Apple iPhone 15 Plus eSIM 128GB 26.0 MTXR3LL/A Cracked Back Broken Unlocked Apple iPhone 15 Plus eSIM 128GB 26.0 MTXR3LL/A Cracked Back Items included ...|should_be_priority_review|Priority Review|medium|Use whole-phone evidence phrases: unlocked, for parts, cracked back, back glass|
|v1\|287359768873\|0|Apple iPhone 15 Plus 128GB – UNLOCKED – 87% Battery –Cracked Back Deep Scratches|317.5|Needs Data / Needs Review|Expected profit below threshold; Only upside case works; Conservative profit is negative; Low-confidence pricing needs stronger profit; Profit depends on mint resale; ...|For sale is a used iPhone 15 Plus 128GB (Unlocked). No iCloud account Screen: Deep scratches\ Back: Cracked\ Face ID: Working\ Battery Health: 87%\ Digitizer, LCD, cha...|should_be_priority_review|Priority Review|medium|Use whole-phone evidence phrases: unlocked, no icloud, for parts, as-is, cracked back, back glass, charging port|
|v1\|358610883225\|0|Apple iPhone 15 128GB T-Mobile Blue Cracked Screen Glass|279.95|Needs Data / Needs Review|Expected profit below threshold; Profit depends on mint resale; Parts estimate not verified; Low-confidence pricing|Apple iPhone 15 Color: Blue Capacity: 128GB Carrier: T-Mobile Only Clean IMEI FMI / iCloud OFF Condition: Parts/Repair Only. Details: Cracked Screen Glass. Cracked Bac...|should_be_priority_review|Priority Review|medium|Use whole-phone evidence phrases: clean imei, for parts, cracked screen, cracked back, back glass|
|v1\|358610877995\|0|Apple iPhone 14 Pro 256GB Unlocked Silver Cracked Back Glass|379.95|Needs Data / Needs Review|Expected profit below threshold; Profit depends on mint resale; Parts estimate not verified|Apple iPhone 14 Pro Color: Silver Capacity: 256GB Carrier: Unlocked Clean IMEI FMI / iCloud OFF Condition: Parts/Repair Only. Details: Cracked Back Glass Fully functio...|should_be_priority_review|Priority Review|medium|Use whole-phone evidence phrases: clean imei, unlocked, for parts, cracked back, back glass|
|v1\|358610874677\|0|Apple iPhone 13 128GB Unlocked Midnight Cracked Back Glass|189.95|Needs Data / Needs Review|Expected profit below threshold; Only upside case works; Conservative profit is negative; Low-confidence pricing needs stronger profit; Parts estimate not verified; Lo...|Apple iPhone 13 128GB Unlocked Midnight Cracked Back Glass|should_be_priority_review|Priority Review|medium|Use whole-phone evidence phrases: unlocked, for parts, cracked back, back glass|
|v1\|267683056874\|0|Apple iPhone 13 - 128 GB - Midnight (Unlocked) - BAD BATTERY|179.99|Needs Data / Needs Review|Expected profit below threshold; Only upside case works; Conservative profit is negative; Low-confidence pricing needs stronger profit; Profit depends on mint resale; ...|Device is in very bad condition with billions of scratches (sides and edges have heavy wear). Device has a low battery health, other than that, it works fine. Device i...|should_be_priority_review|Priority Review|medium|Use whole-phone evidence phrases: works, unlocked, for parts, bad battery|
|v1\|267677246485\|0|Apple iPhone 14 Pro Max - 1 TB - Gold (Unlocked) - Bad Battery|499.99|Needs Data / Needs Review|Expected profit below threshold; Only upside case works; Low-confidence pricing needs stronger profit; Profit depends on mint resale; Parts estimate not verified; Low-...|Screen has scratches all over. Battery health is 75% and says service is needed. Everything else remains Functional. iPhone only. P34 - for listing reference only|should_be_priority_review|Priority Review|medium|Use whole-phone evidence phrases: unlocked, for parts, bad battery|
|v1\|298360265676\|0|Apple iPhone S Model A1633 Locked, No Sim, Cracked Screen, Powers On|14.75|Needs Data / Needs Review|Parts estimate not verified; Model unknown; Best Offer available|Apple iPhone S Model A1633 Locked, No Sim, Cracked Screen, Powers On|should_be_priority_review|Priority Review|medium|Use whole-phone evidence phrases: powers on, for parts, cracked screen|
|v1\|307050354805\|0|Apple iPhone 15 - 128GB - Green (Unlocked) - CRACKED BACK, NON OEM SCREEN|249.99|Needs Data / Needs Review|Expected profit below threshold; Only upside case works; Low-confidence pricing needs stronger profit; Profit depends on mint resale; Parts estimate not verified; Low-...|SEARCH OUR INVENTORY For Parts or Not Working Apple iPhone 15 - 128GB - Green (Unlocked) Specs: Unlocked Included: Device NOT Included: SIM Card Charger Headphones Ori...|should_be_priority_review|Priority Review|medium|Use whole-phone evidence phrases: unlocked, for parts, as-is, no returns, cracked back, back glass, charging port|
|v1\|147336172208\|0|Apple iPhone 12 64GB A2172 Black (Unlocked) Smartphone - Back Glass Cracked|119.95|Needs Data / Needs Review|Parts estimate not verified|Apple iPhone 12 64GB A2172 Black (Unlocked) Smartphone - Back Glass Cracked|should_be_priority_review|Priority Review|medium|Use whole-phone evidence phrases: unlocked, for parts, back glass|
|v1\|358608017324\|0|Apple iPhone 11 64GB Red Unlocked/T-Mobile 12MP Cracked Screen For Parts|80.0|Needs Data / Needs Review|Parts estimate not verified; Best Offer available|Apple iPhone 11 64GB Red Unlocked/T-Mobile 12MP Cracked Screen For Parts|should_be_priority_review|Priority Review|medium|Use whole-phone evidence phrases: unlocked, for parts, cracked screen|
|v1\|327181950804\|0|Apple iPhone SE 2nd Gen 128GB A2275 White Unlocked Cracked Screen #A88|79.95|Needs Data / Needs Review|Parts estimate not verified|Apple iPhone SE 2nd Gen 128GB A2275 White Unlocked Cracked Screen #A88|should_be_priority_review|Priority Review|medium|Use whole-phone evidence phrases: unlocked, for parts, cracked screen|
|v1\|397997400582\|0|Broken Unlocked Apple iPhone 12 64GB 26.4.2 MMPT3LL/A Cracked Screen|99.99|Needs Data / Needs Review|Parts estimate not verified|Broken Unlocked Apple iPhone 12 64GB 26.4.2 MMPT3LL/A Cracked Screen|should_be_priority_review|Priority Review|medium|Use whole-phone evidence phrases: unlocked, for parts, cracked screen|
|v1\|306966623047\|0|Apple iPhone XR - 64GB - Blue (Unlocked) - CRACKED SCREEN|89.99|Needs Data / Needs Review|Parts estimate not verified|Apple iPhone XR - 64GB - Blue (Unlocked) - CRACKED SCREEN|should_be_priority_review|Priority Review|medium|Use whole-phone evidence phrases: unlocked, for parts, cracked screen|
|v1\|298359608183\|0|Apple iPhone 11 Black 128GB Unlocked - Cracked Screen - Dark LCD - READ|132.59|Needs Data / Needs Review|Parts estimate not verified|Apple iPhone 11 Black 128GB Unlocked - Cracked Screen - Dark LCD - READ|should_be_priority_review|Priority Review|medium|Use whole-phone evidence phrases: unlocked, for parts, cracked screen|
|v1\|278027791351\|0|Apple iPhone XR Black 128GB Unlocked -Face ID NF/Battery/LCD- Back Glass Cracked|86.69|Needs Data / Needs Review|Parts estimate not verified|Apple iPhone XR Black 128GB Unlocked -Face ID NF/Battery/LCD- Back Glass Cracked|should_be_priority_review|Priority Review|medium|Use whole-phone evidence phrases: unlocked, for parts, back glass|
|v1\|366436115269\|0|Broken Unlocked Apple iPhone SE 2nd Gen 64GB 26.3 MHGQ3J/A Bad Battery|59.99|Needs Data / Needs Review|Parts estimate not verified|Broken Unlocked Apple iPhone SE 2nd Gen 64GB 26.3 MHGQ3J/A Bad Battery|should_be_priority_review|Priority Review|medium|Use whole-phone evidence phrases: unlocked, for parts, bad battery|
|v1\|358602365888\|0|Broken Unlocked Apple iPhone 12 64GB 26.5 MGH93LL/A Bad Battery|129.99|Needs Data / Needs Review|Parts estimate not verified|Broken Unlocked Apple iPhone 12 64GB 26.5 MGH93LL/A Bad Battery|should_be_priority_review|Priority Review|medium|Use whole-phone evidence phrases: unlocked, for parts, bad battery|
|v1\|406947795392\|0|Apple iPhone 11 - 64GB Space Gray (Unlocked) - Back Glass Cracked|120.0|Needs Data / Needs Review|Parts estimate not verified|Apple iPhone 11 - 64GB Space Gray (Unlocked) - Back Glass Cracked|should_be_priority_review|Priority Review|medium|Use whole-phone evidence phrases: unlocked, for parts, back glass|

## 4. Wasted Needs Data Summary

- Total Needs Data audited: 139
- Truly needs data: 79
- Could auto-reject: 4
- Could auto-avoid hard risk: 0
- Could be Watch/Priority Review: 39
- Could be Best Pick/Candidate: 1
- Missing pricing table: 12
- Missing description fetch/raw description: 123
- Percent wasted: 31.7%
- Top repeated causes: parts cost missing/pricing issue=136, description missing=123, specific repair issue missing=76, repair issue missing=75, resale missing=70

## 5. Possible Missed Gems

|item_id|title|total_cost|model|storage|repair_issue|profit_mid|current_bucket|trace_blocking_rules|key_description_evidence|suggested_signal_rule|
|---|---|---|---|---|---|---|---|---|---|---|
|v1\|147334281535\|0|Apple iPhone 13 Pro 128GB A2483 Sierra Blue (Unlocked) - Back Glass Cracked|199.95|iPhone 13 Pro|128GB|back_glass_cracked, unlocked|94.05|Needs Data / Needs Review||Home Add to favorites Feedback Contact Tablets Smartwatches Game Consoles Laptops & Computers Accessories Cell Phones Networking Computer Components Monitors Apple iPh...|Use whole-phone evidence phrases: unlocked, no icloud, for parts, back glass|

## 6. Possible False Positives

|item_id|title|total_cost|current_bucket|score|alert_eligible|hard_reject_flags|matched_phrases|why|suggested_signal_rule|
|---|---|---|---|---|---|---|---|---|---|
|v1\|327253131790\|0|Apple iPhone 14 oem cracked screen parts Read bad OLED|40.0|Best Pick / Candidate|93.0|True||works, phone is not included, for flex parts only, for parts, as is, no returns, cracked screen, bad oled|Stored text or flags indicate accessory/component-only listing.|Auto-sort using phrase/flag evidence: works, phone is not included, for flex parts only, for parts, as is, no returns, cracked screen, bad oled|

## 7. Description Signals the App Is Missing

|phrase|count|category|example_item_ids|currently_handled_as|should_interpret_as|
|---|---|---|---|---|---|
|for parts|137|still_needs_manual_review|v1\|267721455090\|0, v1\|257612607490\|0, v1\|158067232486\|0, v1\|298490998367\|0, v1\|307050354805\|0|{'Rejected / Avoid': 38, 'Priority Review': 2, 'Needs Data / Needs Review': 95, 'Best Pick / Candidate': 1, 'Risky': 1}|Review-only unless paired with hard risk/component language.|
|unlocked|79|positive_signal|v1\|298490998367\|0, v1\|307050354805\|0, v1\|366530441592\|0, v1\|398154164118\|0, v1\|178173227672\|0|{'Needs Data / Needs Review': 61, 'Priority Review': 1, 'Rejected / Avoid': 16, 'Risky': 1}|Use as whole-phone/positive evidence; never alert by itself.|
|back glass|32|repair_signal|v1\|307050354805\|0, v1\|178173192434\|0, v1\|147337634377\|0, v1\|800086544988\|0, v1\|287359768873\|0|{'Needs Data / Needs Review': 22, 'Rejected / Avoid': 9, 'Risky': 1}|Use as repair issue signal; not whole-phone proof by itself.|
|cracked screen|31|repair_signal|v1\|158067232486\|0, v1\|298490998367\|0, v1\|127964300103\|0, v1\|398154164118\|0, v1\|327253131790\|0|{'Priority Review': 2, 'Needs Data / Needs Review': 15, 'Best Pick / Candidate': 1, 'Rejected / Avoid': 12, 'Risky': 1}|Use as repair issue signal; not whole-phone proof by itself.|
|read description|26|still_needs_manual_review|v1\|188436755988\|0, v1\|336607582838\|0, v1\|336607567691\|0, v1\|188436678982\|0, v1\|117220100178\|0|{'Needs Data / Needs Review': 21, 'Rejected / Avoid': 5}|Review-only unless paired with hard risk/component language.|
|bad battery|22|repair_signal|v1\|157950065777\|0, v1\|327183323420\|0, v1\|287359618203\|0, v1\|267683056874\|0, v1\|358606746046\|0|{'Needs Data / Needs Review': 17, 'Rejected / Avoid': 5}|Use as repair issue signal; not whole-phone proof by itself.|
|cracked back|19|repair_signal|v1\|307050354805\|0, v1\|178173192434\|0, v1\|800086544988\|0, v1\|287359768873\|0, v1\|377217833231\|0|{'Needs Data / Needs Review': 14, 'Rejected / Avoid': 5}|Use as repair issue signal; not whole-phone proof by itself.|
|powers on|10|whole_phone_evidence|v1\|178173192434\|0, v1\|287359635450\|0, v1\|298360265676\|0, v1\|800080984356\|0, v1\|358606746046\|0|{'Rejected / Avoid': 5, 'Needs Data / Needs Review': 5}|Use as whole-phone/positive evidence; never alert by itself.|
|works|10|whole_phone_evidence|v1\|267721455090\|0, v1\|257612607490\|0, v1\|298490998367\|0, v1\|327253131790\|0, v1\|178173227672\|0|{'Rejected / Avoid': 3, 'Needs Data / Needs Review': 6, 'Best Pick / Candidate': 1}|Use as whole-phone/positive evidence; never alert by itself.|
|as-is|8|still_needs_manual_review|v1\|307050354805\|0, v1\|287359768873\|0, v1\|800080984356\|0, v1\|306966558925\|0, v1\|800079864468\|0|{'Needs Data / Needs Review': 3, 'Rejected / Avoid': 4, 'Risky': 1}|Review-only unless paired with hard risk/component language.|
|clean imei|7|positive_signal|v1\|158067232486\|0, v1\|178173146967\|0, v1\|358610883225\|0, v1\|358610877995\|0, v1\|358606746046\|0|{'Priority Review': 1, 'Rejected / Avoid': 1, 'Needs Data / Needs Review': 5}|Use as whole-phone/positive evidence; never alert by itself.|
|camera lens|4|repair_signal|v1\|800080984356\|0, v1\|800079864468\|0, v1\|800076170248\|0, v1\|800076015436\|0|{'Rejected / Avoid': 4}|Use as repair issue signal; not whole-phone proof by itself.|
|as is|3|still_needs_manual_review|v1\|327253131790\|0, v1\|287359635450\|0, v1\|377217695310\|0|{'Best Pick / Candidate': 1, 'Needs Data / Needs Review': 2}|Review-only unless paired with hard risk/component language.|
|no returns|3|soft_risk|v1\|307050354805\|0, v1\|327253131790\|0, v1\|306966558925\|0|{'Needs Data / Needs Review': 1, 'Best Pick / Candidate': 1, 'Risky': 1}|Review-only unless paired with hard risk/component language.|
|bad oled|3|repair_signal|v1\|327253131790\|0, v1\|178173192434\|0, v1\|287359635450\|0|{'Best Pick / Candidate': 1, 'Rejected / Avoid': 1, 'Needs Data / Needs Review': 1}|Use as repair issue signal; not whole-phone proof by itself.|
|charging port|3|repair_signal|v1\|307050354805\|0, v1\|287359768873\|0, v1\|306966558925\|0|{'Needs Data / Needs Review': 2, 'Risky': 1}|Use as repair issue signal; not whole-phone proof by itself.|
|no icloud|2|whole_phone_evidence|v1\|287359768873\|0, v1\|147334281535\|0|{'Needs Data / Needs Review': 2}|Use as whole-phone/positive evidence; never alert by itself.|
|reset|2|whole_phone_evidence|v1\|178173192434\|0, v1\|178173146967\|0|{'Rejected / Avoid': 2}|Use as whole-phone/positive evidence; never alert by itself.|
|display assembly|2|accessory_reject|v1\|267721455090\|0, v1\|257612607490\|0|{'Rejected / Avoid': 2}|Reject or hard-block when paired with part/component context.|
|screen display assembly|2|accessory_reject|v1\|267721455090\|0, v1\|257612607490\|0|{'Rejected / Avoid': 2}|Reject or hard-block when paired with part/component context.|
|untested|2|soft_risk|v1\|358608706751\|0, v1\|198369182173\|0|{'Needs Data / Needs Review': 1, 'Rejected / Avoid': 1}|Review-only unless paired with hard risk/component language.|
|face id works|1|whole_phone_evidence|v1\|157916351415\|0|{'Needs Data / Needs Review': 1}|Use as whole-phone/positive evidence; never alert by itself.|
|erased|1|whole_phone_evidence|v1\|178173192434\|0|{'Rejected / Avoid': 1}|Use as whole-phone/positive evidence; never alert by itself.|
|mdm|1|hard_risk|v1\|178173192434\|0|{'Rejected / Avoid': 1}|Block or reject unless clearly negated.|
|oled only|1|accessory_reject|v1\|327253086710\|0|{'Rejected / Avoid': 1}|Reject or hard-block when paired with part/component context.|
|phone is not included|1|accessory_reject|v1\|327253131790\|0|{'Best Pick / Candidate': 1}|Reject or hard-block when paired with part/component context.|
|for flex parts only|1|accessory_reject|v1\|327253131790\|0|{'Best Pick / Candidate': 1}|Reject or hard-block when paired with part/component context.|

## 8. Parsing Failures

|item_id|title|app_detected|correct_value|fix|
|---|---|---|---|---|
|v1\|267721455090\|0|OEM Apple iPhone 16 Screen Display Assembly Cracked Glass Good OLED Touch Works|unknown|iPhone 16|Parse model from title/aspects unless accessory compatibility text is present.|
|v1\|257612607490\|0|OEM Apple iPhone 14 Screen Display Assembly Cracked Glass Good OLED Touch Works|unknown|iPhone 14|Parse model from title/aspects unless accessory compatibility text is present.|
|v1\|327253086710\|0|Apple iPhone X oem cracked screen OLED only parts READ|unknown|iPhone X|Parse model from title/aspects unless accessory compatibility text is present.|
|v1\|336607524405\|0|Apple iPhone 17e A3575 Black 256GB A18 12MP Dual SIM GSM Smartphone for parts|unknown|iPhone 17|Parse model from title/aspects unless accessory compatibility text is present.|
|v1\|178172004209\|0|APPLE IPHONE 16e AT&T 128GB BLACK DEFECTIVE READ DESCRIPTION|unknown|iPhone 16|Parse model from title/aspects unless accessory compatibility text is present.|
|v1\|227361690157\|0|*PARTS/REPAIR* Lot of 11 Apple iPhone XR A1984 (PLEASE READ DESCRIPTION)|unknown|iPhone X|Parse model from title/aspects unless accessory compatibility text is present.|

## 9. Pricing / Data Gaps

|model|repair_type|count|example_item_ids|causing_real_waste|
|---|---|---|---|---|
|unknown|unknown|22|v1\|137353691233\|0, v1\|157950053100\|0, v1\|178172004209\|0, v1\|178173042392\|0, v1\|188436526270\|0, v1\|188436530687\|0, v1\|227361690157\|0, v1\|236843273922\|0|True|
|iPhone 12|back_glass_cracked, unlocked|5|v1\|147336173071\|0, v1\|147336178872\|0, v1\|358610800609\|0, v1\|358610810209\|0, v1\|358610849188\|0|True|
|unknown|cracked_screen|4|v1\|147337003790\|0, v1\|147337007751\|0, v1\|287359416891\|0, v1\|327253086710\|0|True|
|iPhone 17 Pro Max|unknown|4|v1\|236842241764\|0, v1\|318372845085\|0, v1\|318372911450\|0, v1\|327183275011\|0|True|
|iPhone 15|back_glass_cracked, unlocked|3|v1\|287359768873\|0, v1\|307050354805\|0, v1\|800086544988\|0|True|
|iPhone 11|unlocked|3|v1\|178173125924\|0, v1\|188436678982\|0, v1\|227362492358\|0|True|
|unknown|unlocked|3|v1\|236843211122\|0, v1\|257532522623\|0, v1\|336607241842\|0|True|
|iPhone 13|unlocked|2|v1\|127890416405\|0, v1\|178173227672\|0|True|
|iPhone 12|unlocked|2|v1\|157950019678\|0, v1\|188436755988\|0|True|
|unknown|cracked_screen, unlocked|2|v1\|188436379113\|0, v1\|306968636089\|0|True|
|iPhone SE|unlocked|2|v1\|178172974189\|0, v1\|206305696592\|0|True|
|iPhone XR|unknown|2|v1\|358611171797\|0, v1\|406958075616\|0|True|
|iPhone 11|bad_battery, unlocked|2|v1\|157950065777\|0, v1\|327183323420\|0|True|
|iPhone XS Max|unknown|2|v1\|137355214383\|0, v1\|406957162308\|0|True|
|iPhone 15 Pro|unlocked|2|v1\|117220057135\|0, v1\|267683233857\|0|True|
|iPhone 13 Mini|unlocked|2|v1\|278030046755\|0, v1\|377217695310\|0|True|
|iPhone 12|unknown|2|v1\|398000267107\|0, v1\|406958058300\|0|True|
|iPhone 14 Pro|unknown|2|v1\|236843280691\|0, v1\|406958065630\|0|True|
|iPhone 13|unknown|2|v1\|188435297748\|0, v1\|257532583379\|0|True|
|iPhone X|cracked_screen, unlocked|1|v1\|366530441592\|0|False|
|iPhone 13|cracked_screen|1|v1\|127964300103\|0|False|
|iPhone 14|cracked_screen, bad_oled|1|v1\|327253131790\|0|False|
|iPhone 11|screen_display_issue, back_glass_cracked|1|v1\|147337634377\|0|False|
|iPhone 13 Pro Max|unlocked|1|v1\|366440785411\|0|False|
|iPhone XS Max|unlocked|1|v1\|366440785393\|0|False|
|iPhone XR|cracked_screen|1|v1\|178173083409\|0|False|
|iPhone 15|back_glass_cracked|1|v1\|377217833231\|0|False|
|iPhone 13 Pro|unknown|1|v1\|117220112327\|0|False|
|iPhone 12 Mini|unknown|1|v1\|137355261204\|0|False|
|iPhone 13 Pro Max|unlocked, clean_imei|1|v1\|117220100178\|0|False|

## 10. Recommended Deterministic Rule Ideas

|rule idea|examples|expected effect|risk|priority|
|---|---|---|---|---|
|Auto-sort clear accessory/component or hard-risk descriptions out of Needs Data|v1\|267721455090\|0, v1\|257612607490\|0, v1\|327253131790\|0, v1\|327253086710\|0|Reduce wasted review for non-phone parts and hard-risk locked/blocked devices.|Medium; phrase negation and accessory compatibility text need guardrails.|high|
|Fill repeated pricing/resale table gaps|v1\|137353691233\|0, v1\|157950053100\|0, v1\|178172004209\|0, v1\|178173042392\|0, v1\|188436526270\|0, v1\|188436530687\|0, v1\|227361690157\|0, v1\|236843273922\|0,...|Convert pricing-driven Needs Data into Watch/Priority/Reject.|Low if pricing data is researched.|medium|
|Improve title/aspect model and storage parsing with accessory guardrails|v1\|267721455090\|0, v1\|257612607490\|0, v1\|327253086710\|0, v1\|336607524405\|0, v1\|178172004209\|0|Reduce model/storage unknown review.|Medium; accessory compatibility can be mistaken for phone model.|medium|

## 11. Recommended Regression Tests

- Candidate listings with accessory/hard-risk phrases should become alert-ineligible/rejected in raw-rescore.
- Clear title/aspect model/storage examples should parse unless accessory compatibility terms are present.
- Pricing gap examples should remain Needs Data until researched pricing data exists.
- Whole-phone evidence phrases should reduce Needs Data only when no hard-risk/accessory phrase is present.

## 12. Final Recommendation

One small deterministic patch recommended.

Raw-rescore dry run summary, with scan-cycle writes disabled in process:
- items_rescored: 50
- changed_status_count: 2
- changed_alert_eligibility_count: 1
- alert_eligible_count: 0
- Changed items: v1|307050354805|0 risky->rejected, v1|327253131790|0 candidate->rejected
