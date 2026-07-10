# Solar Buddy 🌞

En HACS custom integration til Home Assistant, der optimerer udnyttelsen af
lokal solcelleproduktion ved at koordinere husets forbrug, solcelleproduktion,
husbatteri, elbil/EV-lader og aktuelle samt fremtidige elpriser.

Solar Buddy er **deterministisk og kører 100 % lokalt**. Der er ingen cloud,
ingen ekstern server og ingen AI-model involveret i driften.

> **Vigtigt:** Solar Buddy starter i **Monitor only**. Den sender ingen
> styringskommandoer, før du selv vælger en aktiv strategi **og** tænder
> kontakten *Automatisk styring*. Kontakten er altid slukket efter
> installation, opdatering og genstart — Solar Buddy aktiverer aldrig sig selv.

## Funktionsoversigt

- Beregner aktuelt soloverskud og hvor meget effekt der er til rådighed til
  batteri og elbil.
- Anbefaler ladestrøm til elbilen (afrundet til dit strømtrin, begrænset af
  min/max).
- Klassificerer elprisen relativt (percentiler) og kan anbefale netopladning
  i billige perioder, når bilen er under sin minimum-SoC.
- Fire strategier: *Solar only*, *Price aware*, *Balanced*, *Monitor only*.
- Tre prioriteter: *Battery first*, *EV first*, *Balanced*.
- Alle beregninger eksponeres som sensorer, så du kan bygge dashboards og
  automationer oven på dem — også uden automatisk styring.

## Installation gennem HACS

1. HACS → ⋮ → *Custom repositories* → tilføj
   `https://github.com/MartinJensenDK/solar-buddy` som *Integration*.
2. Installér **Solar Buddy** og genstart Home Assistant.
3. *Indstillinger → Enheder & tjenester → Tilføj integration* → søg efter
   "Solar Buddy".

## Manuel installation

Kopiér `custom_components/solar_buddy/` til `config/custom_components/` i din
Home Assistant-installation og genstart.

## Første opsætning

Opsætningen sker i trin. Kun de to energisensorer er obligatoriske:

| Trin | Felt | Krav |
|------|------|------|
| Energi | `solar_production_entity` | Effekt-sensor i W eller kW (ikke kWh). Negative værdier behandles som 0. |
| Energi | `house_consumption_entity` | Husets **grundforbrug** i W/kW — se nedenfor. |
| Batteri | `battery_enabled` | Til/fra. |
| Batteri | `battery_soc_entity` | Valgfri, procent 0–100. |
| Batteri | `battery_power_mode` | `signed`, `separate` eller `none`. |
| Batteri (signed) | `battery_power_entity` + `battery_power_sign` | Én sensor med fortegn — du vælger hvad plus/minus betyder. |
| Batteri (separate) | `battery_charge_power_entity` + `battery_discharge_power_entity` | To separate effekt-sensorer. |
| Batteri | `battery_charging_enabled_entity` | Valgfri. Sensor/binary_sensor (status) eller switch/input_boolean (styring). |
| Batteri | `battery_charge_limit_entity` | Valgfri. Procent-sensor (status) eller number/input_number/select (styring). |
| EV | `ev_charger_enabled` | Til/fra. |
| EV | `ev_control_type` | Én switch **eller** separate start/stop-entiteter (button, script, switch). |
| EV | `ev_charger_current_entity` | Valgfri. Skrivbar number/input_number i ampere. Uden den vises anbefalingen kun. |
| EV | `ev_cable_connection_entity` | Valgfri. Binary_sensor (`on` = tilsluttet) eller enum-sensor + liste af "tilsluttet"-tilstande. |
| EV | `ev_soc_entity` / `ev_min_soc_entity` | Valgfri, procent 0–100. Minimum kan være en read-only sensor (læses, ændres aldrig). |
| Elpris | `electricity_price_entity` | Valgfri. Fx Energi Data Service. |

### Husets grundforbrug — vigtigt

Husforbrugs-sensoren skal måle husets forbrug **uden** EV-laderen og uden
batteriets op-/afladning. Solar Buddy indregner batteri og EV separat, så
belastningerne ikke dobbeltregnes, og så bilens egen opladning ikke "æder"
sit eget overskud i beregningen.

### Batteriets fortegn

Inverter-mærker er uenige om, hvad fortegnet på en batteri-effekt-sensor
betyder. Ved `signed` vælger du derfor:

- `positive_is_charging`: +2000 W = oplader med 2000 W; −1500 W = aflader med 1500 W.
- `positive_is_discharging`: det omvendte.

Internt normaliseres altid til to ikke-negative værdier:
`battery_charge_power_w` og `battery_discharge_power_w`.

Bemærk: Batteriafladning regnes **aldrig** som gratis soloverskud til bilen.
Netopladning anbefales kun af prisreglerne (billige timer + bil under minimum).

## Understøttede enheder

Effekt-sensorer accepteres i W og kW (MW accepteres også) og normaliseres
internt til watt. En sensor uden enhed antages at være i watt. Akkumulerede
energisensorer (Wh/kWh/MWh) afvises i opsætningen.

## EV-styring

Automatisk EV-styring sender kun kommandoer, når **alle** disse betingelser
er opfyldt: automatisk styring er tændt, strategien ikke er *Monitor only*,
laderen er konfigureret, kabelstatus er kendt og bilen tilsluttet, de
obligatoriske sensorer leverer friske gyldige værdier, den beregnede handling
har været stabil i start-/stopforsinkelsen, og minimumsintervallet siden
sidste kommando er udløbet.

Anbefalet ladestrøm beregnes som
`floor(tilgængelig_effekt / (faser × spænding × trin)) × trin`, begrænset til
[min, max]. Er der ikke effekt nok til minimumsstrømmen, stoppes opladningen
efter stopforsinkelsen.

**Hysterese:** Et kortvarigt soludsving starter ikke opladningen (kravet skal
være stabilt i hele startforsinkelsen), en enkelt sky stopper den ikke
(stopforsinkelsen), og strømmen justeres højst én gang pr.
justeringsinterval og kun når ændringen er mindst ét strømtrin.

**Rækkefølge:** Ved start sættes ladestrømmen først (hvis entiteten er
skrivbar), derefter startes opladningen, og Solar Buddy verificerer bagefter
at kontakten faktisk skiftede tilstand — udebliver bekræftelsen, logges en
advarsel, og der sendes ingen nye kommandoer imens. Ved stop sendes ingen
strømjusteringer, før opladningen startes igen.

**Manuel overstyring:** Ændrer du selv en styret lader-entitet (fx slukker
kontakten fra UI'et), sætter Solar Buddy automatikken på pause i
`manual_override_pause` minutter, så den ikke kæmper imod dig. Status-sensoren
viser *På pause (manuel overstyring)*, og knappen *Ryd manuel overstyring*
ophæver pausen med det samme. Solar Buddys egne kommandoer genkendes via
deres context og udløser aldrig pausen.

## Energi Data Service

Solar Buddy har særlig understøttelse af
[Energi Data Service](https://github.com/MTrab/energidataservice):
`raw_today`/`raw_tomorrow` parses dynamisk (times- eller 15-minutters
intervaller, 23/25-timers sommertidsdage, huller og dubletter håndteres),
`raw_tomorrow` bruges kun når `tomorrow_valid` er sand, og valuta/enhed/pris
tages 1:1 fra sensoren — inkl. de tariffer og afgifter, den allerede har lagt på.

Prisniveauet (*very_cheap* … *very_expensive*) beregnes relativt med
percentiler (standard: billigste 25 % / dyreste 25 %), så det virker uanset
valuta og prisniveau. Negative priser er altid *very_cheap*.

## Strategier

- **Solar only**: EV må kun bruge beregnet soloverskud; ingen planlagt netimport.
- **Price aware**: Netopladning tillades i billige perioder, når bilen er
  under sin minimum-SoC; unødvendig opladning undgås i dyre perioder.
- **Balanced**: Soloverskud først, billige priser som supplement, minimum-SoC
  sikres når data findes.
- **Monitor only** *(standard)*: Alt beregnes og vises; intet styres.

**Prioriteter:** *Battery first* reserverer batteriets aktuelle ladeeffekt før
bilen får resten; *EV first* giver bilen hele overskuddet; *Balanced* løfter
bilen over batteriet når bilen er under sin minimum-SoC, og batteriet over
bilen når batteriet er under sin reserve-SoC.

## Entiteter

| Type | Entitet | Formål |
|------|---------|--------|
| sensor | Status, Recommendation | Forklaring af den aktuelle beslutning (oversatte tilstande; talværdier som attributter) |
| sensor | Solar surplus, Available EV power (W) | Effektbalancen |
| sensor | Recommended EV current (A) | Anbefalet ladestrøm |
| sensor | Current electricity price, Price level | Elpris med valuta/enhed fra kilden |
| sensor | Next action, Last evaluation, Last command | Diagnostik (tidsstempler) |
| binary_sensor | Data ready, Solar surplus available, EV connected, Automatic control available, Manual override | Datakvalitet og status |
| switch | Automatic control | Hovedafbryder — altid slukket efter genstart |
| select | Strategy, Priority | Driftsform |
| button | Recalculate, Clear manual override | Manuel genberegning / ryd pause |

## Sikkerhed og failsafe

Solar Buddy sender ingen kommandoer hvis: en obligatorisk sensor er
`unknown`/`unavailable` eller ikke kan konverteres til watt; data er ældre end
`data_stale_timeout`; kabelstatus er ukendt; strøm-entiteten ikke er skrivbar;
automatisk styring er slukket. Fejl i valgfrie batterientiteter stopper ikke
integrationen — de logges én gang ved skift til utilgængelig og én gang ved
skift tilbage.

## Typiske konfigurationer

- **Kun overvågning:** Vælg kun de to energisensorer. Du får overskuds- og
  anbefalingssensorer til dashboards.
- **Solcelle + elbil:** Tilføj EV-lader (switch + strøm-entitet + kabelstatus)
  og sæt strategien til *Solar only*.
- **Fuldt setup:** Batteri (signed effekt-sensor), EV med min-SoC fra bilens
  integration, Energi Data Service-sensor og strategien *Balanced*.

## Fejlfinding

- **Status viser "Venter på data":** Tjek at begge energisensorer findes og
  har numerisk state og enhed W/kW.
- **"Sensordata er forældede":** Kilden opdaterer sjældnere end
  `data_stale_timeout` — hæv den under *Konfigurér → Indstillinger*.
- **Prisniveau er "Ukendt":** Elpris-sensoren mangler `raw_today`, eller der
  er for få intervaller (< 4).
- Diagnostics (⋮ på integrationen → *Download diagnostik*) indeholder den
  fulde normaliserede tilstand og seneste beslutning.

## Kendte begrænsninger

- Batteristyring (skrivning til batteriets kontrol-entiteter) kommer i fase 4;
  batteriet indgår indtil da kun i beregningerne.
- Automatisk EV-styring kræver en konfigureret kabelstatus-entitet — uden
  kendt kabelstatus sendes aldrig kommandoer (failsafe).
- Én elbil pr. installation (multi-EV er planlagt).
- `ev_departure_time` og `ev_battery_capacity_kwh` indsamles, men bruges
  først af planlægningslogikken i en senere fase.

## Sådan fjernes integrationen

*Indstillinger → Enheder & tjenester → Solar Buddy → ⋮ → Slet*. Integrationen
efterlader ingen filer eller hjælpe-entiteter. Fjern derefter evt. selve
HACS-installationen under HACS → Solar Buddy → Fjern.

## Udvikling og test

```bash
pip install -r requirements_test.txt
ruff check .
pytest --cov=custom_components.solar_buddy
```

Rene moduler (`normalization.py`, `price_parser.py`, `optimizer.py`,
`models.py`) kan testes uden Home Assistant-runtime. Config flow-, entity- og
lifecycle-tests bruger `pytest-homeassistant-custom-component`.

## Licens

MIT — se [LICENSE](LICENSE).
