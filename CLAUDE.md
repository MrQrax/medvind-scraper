# Medvind Scraper

Python-scraper som loggar in på Medvind WFM via Microsoft SSO och hämtar jobbschema.
Skickar Windows-notiser kvällen innan ett arbetspass.

## Köra
```
python main.py
```

## Konfiguration
Fyll i `.env` med SSO-lösenord innan körning.

## Beroenden
```
pip install -r requirements.txt
playwright install chromium
```
