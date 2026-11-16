# BMW 330i – codzienny ranking opłacalności (PL + DE)

Raz dziennie pobiera ogłoszenia **BMW 330i z rocznika 2025 i nowszych** z:
- **otomoto.pl** (benzyna, 240–265 KM, bez aut uszkodzonych)
- **autoscout24.de** (silnik 330i, benzyna, Niemcy)

Dla każdego auta liczy **realny koszt zakupu w Polsce** (dla aut z DE: kurs ECB, akcyza 3,1%,
opłaty importowe; auta do 6 mies./6000 km jako „nowy środek transportu” z VAT 23% w PL),
a potem dopasowuje model ceny rynkowej (przebieg, wiek, xDrive, Touring, M Sport) do wszystkich
ogłoszeń. **Ranking = o ile % auto jest tańsze od swojej wartości rynkowej.**

## Aplikacja webowa
```bash
python3 webapp.py             # otwórz http://localhost:8330
./install_webapp.sh           # działa w tle po każdym logowaniu + sama pobiera dane raz dziennie
./uninstall_webapp.sh
```
Ranking z filtrami (PL/DE, xDrive, Touring/Sedan, przebieg, koszt, nowe), wykres koszt–przebieg,
historia cen, ulubione i ukrywanie ogłoszeń, przycisk „Odśwież dane” i ustawienia (osoba prywatna/firma,
koszty importu, akcyza, godzina pobierania, kraje AutoScout24).
Jeśli używasz aplikacji webowej, `install_schedule.sh` nie jest potrzebny.

## Pełna analiza ogłoszenia
Przycisk **Pełna analiza** na każdej karcie otwiera raport: ocena ogólna i cząstkowe (cena, stan i historia,
wyposażenie, sprzedawca), sygnały ostrzegawcze, plusy i minusy, wykryte kluczowe wyposażenie (ACC, kamery 360°,
HUD, Harman Kardon, Comfort Access…) i czego brakuje, pełna lista wyposażenia, dane sprzedawcy (oceny, programy BMW),
rozbicie kosztu importu z DE, pytania do sprzedawcy i opis z ogłoszenia.

**Podsumowanie AI (opcjonalne):** po dodaniu klucza Claude API Claude pisze werdykt, argumenty do negocjacji
(z ceną docelową) i pytania do sprzedawcy.
- w chmurze: GitHub → repozytorium → Settings → Secrets and variables → Actions → `ANTHROPIC_API_KEY`;
  generowane dla najlepszych `ai_top_n` ogłoszeń (domyślnie 25), raz na ogłoszenie (ponownie przy zmianie ceny),
- lokalnie: `pip install anthropic` i `export ANTHROPIC_API_KEY=...`, potem przycisk w oknie analizy.
Model: `ai_model` w `config.json` (domyślnie `claude-opus-5-5`).

## Wersja bez przeglądarki
```bash
python3 tracker.py            # pobierz + raport
python3 tracker.py --report   # tylko przelicz raport z bazy
./install_schedule.sh 8 0     # codziennie o 8:00 (launchd)
./uninstall_schedule.sh
```
Raport: `reports/latest.html` (+ archiwum `ranking-RRRR-MM-DD.html` i `.csv`).
Baza z historią cen: `data/listings.db`. Oznaczenia: **NOWE**, **obniżka X%**, **sprawdź!** (>25% poniżej rynku).

## Ustawienia (`config.json`)
- `buyer_type`: `"private"` (ceny brutto) lub `"company"` (netto, VAT do odliczenia)
- `import_fixed_costs_pln`: transport, tłumaczenia, przegląd, rejestracja (domyślnie 2500 zł)
- `min_year`, `autoscout24_countries` (np. `"D,A"`), `notify_macos`, `open_report_after_run`

## Ograniczenia
- **mobile.de** blokuje automatyczne pobieranie (HTTP 403), dlatego rynek niemiecki pochodzi z AutoScout24.
- Portale zmieniają strukturę stron – jeśli któryś przestanie działać, raport pokaże ostrzeżenie, a reszta zadziała dalej.
- Wyliczenia podatków/akcyzy są orientacyjne. Używaj do prywatnych celów, rzadko (raz dziennie).
