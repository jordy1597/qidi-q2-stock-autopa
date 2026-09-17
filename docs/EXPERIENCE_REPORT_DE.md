# QIDI Q2 Stock-Firmware: automatische Pressure-Advance-Messung mit der vorhandenen Loadcell

**Stand:** 17. September 2026  
**Status:** funktionierender persönlicher Feldversuch, kein Universal-Installationspaket

## Kurzfassung

Auf einem QIDI Q2 mit Stock-QIDI-Firmware wurde die vorhandene Bett-Loadcell (CS1237 über QIDIs `probe_air`) für eine manuell ausgelöste Pressure-Advance-(PA)-Kalibrierung genutzt. Es gab keinen Wechsel auf Mainline-Klipper und keinen Umbau von QIDI Box, Display, RFID oder Homing.

Der CS1237 ist intern auf 1280 Hz konfiguriert. Für ein eigenes Klipper-Extra sind jedoch nur etwa **37 bis 40 Hz** zuverlässig erreichbar. Mit längeren Sweep-Segmenten genügt das für plausible und wiederholbare PA-Werte. Der Ablauf kalibriert beim Filamentwechsel niemals automatisch: Bekannte Materialklassen erhalten nur ihren gespeicherten Wert, unbekannte lösen lediglich eine Meldung aus.

## Was gebaut wurde

| Baustein | Aufgabe |
| --- | --- |
| `q2_loadcell.py` | Isolierter, lesender Adapter für `probe_air.sensor_helper.read_origin_data()`. |
| G0BL1N/autopa | Sweep-Planung und Auswertung der Loadcell-Daten. |
| `qpa_material_db.py` | Persistente Materialklasse → PA-Datenbank, QIDI-Lookup und manuelle Box-Orchestrierung. |
| `qpa_controls.cfg` | Deutsche Makros, sichere Vorbereitung, Stock-QIDI-Purge/Parken und optionaler Box-Rücklauf. |
| `save_variables` | Persistenz ohne PA-Werte in `printer.cfg`. |

Der eigene Sensoradapter verändert weder Probe-Parameter noch Bewegungen, Temperaturen, Extrusion oder die QIDI Box. Er liest ausschließlich den vorhandenen Messwert.

## Sensorzugriff

Der zunächst naheliegende QIDI-Callback `probe_air.add_client(...)` lieferte keine verwendbaren Messdaten, auch nicht während einer echten Probe. Das ist kein Beweis für einen defekten Sensor: QIDIs proprietäre Schnittstelle stellt den internen Datenstrom offenbar nicht an fremde Extras bereit.

Funktioniert hat der direkte, veröffentlichte Stock-Q2-Weg:

```python
printer.lookup_object('probe_air').sensor_helper.read_origin_data()
```

Der Adapter pollt diesen Wert im Klipper-Reactor. Der reine Smoke-Test lautet:

```text
QPA_SENSOR_TEST DURATION=5
```

Er erwartet mindestens 35 Hz und `read_errors=0`; er heizt, bewegt und extrudiert nicht.

## Sichere PA-Kalibrierung

Aktive Q2-Startwerte:

```ini
[autopa]
min_segment_rate: 35
min_segment_samples: 12
sweep_tfast: 0.5
sweep_cycles: 10
sweep_wobble_axis: X
sweep_x_shift: 3
```

Die robuste Sweep-Variante nutzt 17 PA-Kandidaten von 0,020 bis 0,060, fünf Wiederholungen je Kandidat und einen kleinen X-Wobble. Die längeren Segmente gleichen die reale Datenrate von ungefähr 37 Hz aus. Das Wobble ist nötig, da Klipper PA nicht sinnvoll an einem reinen E-Move abbildet.

Ablauf:

1. Drucker muss idle sein.
2. Vorhandene QIDI-Homingroutine ausführen.
3. Z auf mindestens 80 mm anheben.
4. Bettmitte aus echten Achslimits ermitteln und anfahren.
5. Vorhandenen QIDI-Purge nutzen.
6. Sweep mit `APPLY=0` messen.
7. Nur bei gültigem Ergebnis speichern und über `SET_PRESSURE_ADVANCE` für die aktuelle Sitzung anwenden.
8. Über QIDIs vorhandenen Bucket-/`PRINT_END`-Weg aufräumen.

Bei Abbruch oder Fehler wird absichtlich nicht automatisch geschnitten oder entladen.

## Materialdatenbank und QIDI-Materialerkennung

Die Datenbank verwendet nur die Materialklasse, nie Hersteller, Farbe, Rolle oder eine feste Slot-Zuordnung.

```text
PLA Rapido  -> PLA
PLA Matte   -> PLA-MATTE
PETG Rapido -> PETG
PAHT-GF     -> PAHT-GF
```

Die Quelle ist die vorhandene QIDI-Auswahl: `multi_color_controller`, QIDI-`save_variables` und die offizielle Filamentliste. Beim Materialwechsel gilt:

```text
QIDI-Materialauswahl
  -> Materialklasse normalisieren
  -> PA vorhanden? Ja: anwenden
                    Nein: nur informieren
```

Kein unbekanntes Material kann dadurch einen Sweep, eine Box-Aktion, eine Bewegung oder Heizen auslösen.

## Bedienung

Eine zusätzliche native, frei platzierbare Fluidd-Kachel ließ sich im angepassten QIDI-Frontend nicht sauber einbinden. Die Bedienung liegt deshalb im vorhandenen Makros-Widget.

| Makro | Funktion |
| --- | --- |
| `FILAMENT_PA_AKTUELL` | aktiven PA samt QIDI-Material anzeigen |
| `FILAMENT_PA_LISTE` | Datenbank anzeigen |
| `FILAMENT_PA_ANWENDEN` | gespeicherten Wert anwenden |
| `FILAMENT_PA_SETZEN` | Wert manuell speichern und anwenden |
| `FILAMENT_PA_LOESCHEN` | Eintrag entfernen |
| `FILAMENT_PA_KALIBRIEREN` | bereits geladenes unbekanntes Material messen |
| `FILAMENT_PA_NEU_KALIBRIEREN` | vorhandenen Eintrag nach Messung ersetzen |
| `FILAMENT_PA_BOX_A_KALIBRIEREN` bis `..._D_...` | ausgewählten Box-Platz laden, messen und nach Erfolg zurückführen |
| `FILAMENT_PA_RUECKLAUF_STATUS` | Rücklaufvoraussetzung nur lesend prüfen |

Die vier Boxplätze heißen bewusst nur A, B, C und D. Es gibt keine Annahme wie „Slot B ist immer PETG“.

## Box-Ablauf

Der Box-Ablauf ist manuell und folgt diesem Muster:

```text
Box A/B/C/D + MATERIAL + HOTEND
  -> auf HOTEND aufheizen und warten
  -> vorhandenes QIDI EXTRUDER_LOAD
  -> Slot/Filamentsensor prüfen
  -> sichere PA-Vorbereitung + Purge + Sweep
  -> Ergebnis speichern/anwenden
  -> nur bei Erfolg: Stock-QIDI-Entladen in den verifizierten Slot
  -> Stock-QIDI PRINT_END/Park
```

Wichtige Korrektur aus dem Test: QIDIs Lader extrudiert selbst. Die erste Version rief ihn bei kaltem Hotend auf, wodurch Klipper korrekt `Extrude below minimum temp` meldete. Dabei wurde nichts bewegt, nicht gehomt und kein Sweep gestartet. Die Lösung war, vor `EXTRUDER_LOAD` mit `M109 S<Temperatur>` aufzuheizen und zu warten.

## Beobachtete Werte

Diese Werte sind keine Vorgabe für andere Drucker. Sie hängen mindestens von Material, Temperatur, Düse und Extruder ab.

| Materialklasse | Beobachtung |
| --- | --- |
| PLA | ungefähr 0,0283; frühe Läufe lagen bei etwa 0,0292 / 0,0304 / 0,0309 |
| PLA-MATTE | ungefähr 0,0295 |
| PETG | 0,0334 beim letzten erfolgreichen Box-Lauf bei 265 °C |
| PAHT-GF | ungefähr 0,0230 bei 310 °C |

Der letzte PETG-Boxlauf hatte **85 von 85 gültigen Segmenten**, ergab `K_opt = 0,0334` und ersetzte den vorherigen PETG-Datenbankwert. Danach wurde wieder das aktive PLA-Rapido-Profil gewählt und dessen gespeicherter PA angewendet.

## Fehlschläge und Lehren

| Beobachtung | Ursache | Konsequenz |
| --- | --- | --- |
| Keine Daten über `add_client` | Proprietärer QIDI-Callback publiziert den Strom nicht an fremde Extras. | Direkten, lesenden `read_origin_data()`-Weg nutzen. |
| 1280 Hz erwartet, ~37–40 Hz erhalten | ADC-Rate ist nicht gleich Python-Hostrate. | Lange Segmente und Wiederholungen verwenden. |
| PAHT-GF anfangs ohne sinnvolles Ergebnis | Eine Auswertungsmetrik konnte bei überall nullenden Werten NaN erzeugen. | Nullmetrik robust als 0 normalisieren; Offline-Replay prüfen. |
| PAHT-GF reichte über alten Bereich hinaus | Kandidatenbereich war zu klein. | Bereich bis 0,060 erweitert. |
| `Extrude below minimum temp` beim Boxladen | Box-Lader kam vor dem Aufheizen. | Erst M109, dann Stock-Lader. |
| Parameter-Bedienung umständlich | QIDI-/Fluidd-Makros bieten keine native Auswahl-Liste. | Deutsche Makros mit Parameterfeldern und vier Box-Schaltflächen. |
| Q2-Filamentsensor als Durchmessersensor vermutet | Hardware-/Firmwarepfad zeigt digitalen Schalter `THR:PA1`. | Nicht als Filamentbreitenmessung verwenden; Tests zurückbauen. |

## Was sich bewährt hat

- PA per Sensor messen statt per Auge schätzen.
- Flow getrennt behandeln: Der Drucksensor misst Düsendruck, nicht zuverlässig Linienbreite, Wandstärke oder reale Ablage. Ein kurzer Slicer-Testdruck für Flow/Flow Ratio bleibt sinnvoll.
- Slicer-PA deaktivieren: Ein späteres `SET_PRESSURE_ADVANCE` im Start-G-Code überschreibt den Datenbankwert.
- Z-Freiraum, Bettmitte und QIDIs echte Purge-/Parkroutinen sind wichtiger als ein neuer eigener Ablauf.
- Für eine neue Materialrolle ist ein längerer, fein aufgelöster Sweep akzeptabel, wenn man ihn nur einmal ausführt.

## Grenzen und offene Punkte

- 37 Hz sind brauchbar, aber kein Laborinstrument. Kleine Testdrucke bleiben die Plausibilitätskontrolle.
- Der mechanische Rücklauf in jeden einzelnen Box-Slot sollte auf weiteren Firmwareständen und mit mehr Slots reproduziert werden.
- Mehr Wiederholungen mit mehreren Rollen pro Materialklasse fehlen noch.
- Es gibt bewusst keine automatische Flow- oder Durchmesserkorrektur.
- Die Lösung ist Stock-QIDI-spezifisch und kein Mainline-Klipper-Feature.

## Sicherheitsregeln für Nachbauer

- Vor jeder Änderung sichern und einen klaren Rückbauweg notieren.
- Niemals während eines Drucks Klipper neustarten oder Box-/PA-Makros ändern.
- Erst nur den Sensor-Smoke-Test ausführen.
- Rücklauf ausschließlich nach eindeutig bestätigtem Slot und Filamentsensor.
- Bei Abbruch Filament geladen lassen statt den Zielslot zu raten.
- Keine IP-Adressen, WLAN-Daten, Kennwörter, Seriennummern oder komplette ungeprüfte `printer.cfg` veröffentlichen.
- Bei Veröffentlichung von abgeleitetem `autopa`-Code Lizenz und Quellenhinweise beachten.

## Wo veröffentlichen?

1. **Zuerst r/QidiTech3D auf Reddit.** Dort läuft bereits eine aktuelle Diskussion über Q2-/Q2C-/Max4-Loadcell-PA; passend für Erfahrungswerte und Tester.  
   <https://www.reddit.com/r/QidiTech3D/comments/1v642ff/automatic_pressure_advance_calibration_for_the/>

2. **Danach ein eigenes GitHub-Repository.** Das sollte die dauerhafte technische Quelle sein: bereinigte Dateien, Versionsstände, Installation, Rollback, bekannte Einschränkungen und Issues. Erst veröffentlichen, wenn Zugangsdaten entfernt und Lizenzhinweise sauber sind.

3. **Nach einer zweiten erfolgreichen Installation:** QIDI Community Wiki per Pull Request oder Wiki-Seite. Nicht als offizielle QIDI-Anleitung bezeichnen.  
   <https://github.com/qidi-community/q2-wiki>

4. **QIDI Discord ergänzend.** Gut, um Tester zu finden; als langfristige Doku ungeeignet. Dort besser auf Reddit/GitHub verlinken.

## Kopierfertiger Kurzpost

> Ich habe auf einem Stock-QIDI Q2 einen vorsichtigen, manuell ausgelösten Loadcell-PA-Workflow aufgebaut. Kein Firmwarewechsel und kein automatischer Sweep beim Filamentwechsel: Der vorhandene CS1237-/`probe_air`-Pfad wird nur lesend mit real etwa 37–40 Hz abgefragt. Ein Sweep über dem freien Bett speichert PA pro Materialklasse und setzt ihn bei späterer QIDI-Materialauswahl wieder.
>
> Bisher sind PLA, PLA Matte, PETG und PAHT-GF plausibel und wiederholbar. Ein aktueller PETG-Boxlauf bei 265 °C hatte 85/85 gültige Segmente und ergab PA 0,0334. Flow stelle ich weiterhin getrennt per Testdruck im Slicer ein.
>
> Sicherheitsregeln: nur manuell starten, Z mindestens 80 mm, vorhandenen QIDI-Purge/Parkweg verwenden, unbekannte Materialien nur melden. Bei Fehlern/Abbruch wird nicht automatisch entladen. Das ist ein Testprojekt, keine fertige One-Click-Lösung. Ich suche Rückmeldungen von Q2-Besitzern zu Firmwareständen, Wiederholbarkeit und Box-Rücklauf.

## Quellen

- [Klipper: Pressure Advance](https://github.com/Klipper3d/klipper/blob/master/docs/Pressure_Advance.md)
- [Klipper: Load Cells](https://github.com/Klipper3d/klipper/blob/master/docs/Load_Cell.md)
- [G0BL1N/autopa](https://github.com/G0BL1N/autopa)
- [QIDI_Q2](https://github.com/QIDITECH/QIDI_Q2)
- [QIDI Community Q2 wiki](https://github.com/qidi-community/q2-wiki)
- [Reddit: Automatic PA for Q2 / Q2C / Max4](https://www.reddit.com/r/QidiTech3D/comments/1v642ff/automatic_pressure_advance_calibration_for_the/)



