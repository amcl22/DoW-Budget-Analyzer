# Evaluation results

Seed 20261019. Full detail in results.json.

- E1 line accuracy: 199/200 sampled lines pass (99.5%); 512/513 amounts found on the linked page (4 as gross less advance procurement)
- E2 totals: 12/12 published totals match the database
- E3 explanations: 98/100 quotes found on the cited page (2 with extra leading words); 100/100 on a page of the right program
- E4 outliers: 101/101 PB2027 outliers confirmed on the source page
- E5 search: 13/17 top-1, 14/17 top-5; median 82 ms

## E1 failures
- {'id': 20859, 'release': 'PB2026', 'exhibit': 'P-1', 'number': '5112', 'title': 'Ship to Shore Connector', 'failed': ['current_year_amount'], 'pages': [85]}

## E3 failures
- {'id': 18809, 'release': 'PB2026', 'pe': '0300206R', 'page': 87, 'text_found': False, 'pe_on_page': True, 'start': 'Funding has been reallocated to alternative sources. System'}
- {'id': 16195, 'release': 'PB2024', 'pe': '0603618D8Z', 'page': 298, 'text_found': False, 'pe_on_page': True, 'start': 'FY 2024 minimal decrease due to programmatic adjustments. Innovation'}

## E5 searches
- `conventional prompt strike` → rank 1 (top: CONVENTIONAL PROMPT STRIKE (CPS), 112 ms)
- `counterspace` → rank 1 (top: Counterspace Systems, 78 ms)
- `F-35` → rank 1 (top: F-35 C2D2, 76 ms)
- `B-21` → rank 1 (top: B-21 Raider, 74 ms)
- `Columbia submarine` → rank 1 (top: COLUMBIA Class Submarine, 81 ms)
- `collaborative combat aircraft` → rank 1 (top: Collaborative Combat Aircraft, 87 ms)
- `golden dome` → rank 1 (top: Golden Dome for America - Space Programs, 82 ms)
- `hypersonic attack cruise missile` → rank 1 (top: Hypersonics Prototyping - Hypersonic Attack Cruise Missile (HACM), 96 ms)
- `AMRAAM` → rank 1 (top: Advanced Medium Range Air-to-Air Missile (AMRAAM), 82 ms)
- `DDG-51 destroyer` → rank None (top: Ship Preliminary Design & Feasibility Studies, 82 ms)
- `0605518N` → rank 1 (top: CONVENTIONAL PROMPT STRIKE (CPS), 77 ms)
- `narrowband satellite communications` → rank 1 (top: Narrowband Satellite Communications, 94 ms)
- `javelin missile` → rank 1 (top: Anti-Armor Missile-Javelin, 81 ms)
- `aegis missile defense` → rank 2 (top: Ballistic Missile Defense Command and Control, Battle Management and Communications (C2BMC), 124 ms)
- `artificial intelligence army` → rank None (top: Software Engineering Institute, 105 ms)
- `E-7 wedgetail` → rank None (top: None, 78 ms)
- `next generation fighter` → rank 1 (top: Next Generation Fighter, 100 ms)
