# Bridge: Optimierungen und Übertragung in FastVideo

Stand: 11. Oktober 2026. Bridge bleibt ein eigenes Projekt. FastVideo 0.0.3.0 bekommt die nachfolgend passenden Teile.

| Bereich | Bridge-Erkenntnis | FastVideo |
|---|---|---|
| Medienzeit | Windows-QPC (`perf_counter`) für Empfang, Freigabe, Audio und Vorschau; keine Mischung mit grober Monotonic-Zeit | Übernommen. RTP/NTP und Uhrzeit bleiben eigene Zeitbezüge. |
| Bildumwandlung | Ein serieller Arbeitsbereich, begrenzte swscale-Threads, ein gemeinsames NV12-Bild pro Eingangsbild | Übernommen. AV1 und H264 behalten eigene Pixel- und Frame-Metadaten. |
| Encoder | Native Arbeit außerhalb der Steuerschleife, begrenzte Jobs, beim Stoppen zuerst native Arbeit abschließen | Eigene Video-/Audio-Arbeitsbereiche je Publisher. Vorwärmen vor Empfang bleibt. Der spezielle Bridge-Encoderprozess ist nicht erforderlich übertragen worden. |
| Quellstart | Erste alte SDK-Bilder abwarten und gemeinsame echte Quellzeit bestätigen | Übernommen. Keine erfundenen Audio-Zeitstempel. |
| Audio | Gemeinsame Uhr, begrenzter PCM-Puffer, kurze Ankunftstoleranz | Erhalten; FastVideo wartet zusätzlich auf Sample-Ereignisse statt jede Millisekunde zu prüfen. |
| SDI-Eingang | Direkte DeckLink-API, kurze Hardware-Callbacks, getrennt begrenzte Video-/Audioqueue, gemeinsame Hardware-Zeit | Übernommen, Stereo 1+2, progressive 720p/1080p. Desktop Video erforderlich. |
| Status | Ein Hintergrund-Schnappschuss; Geräteabfragen und Warn-Dateizugriffe aus der Medienschleife heraus | Übernommen. Dashboard, Ressourcen und Verbindungsliste teilen einen Sample. Veralteter Sample wird als Fehler gemeldet. |
| NDI/OMT-Eingang | Native SDK-Puffer früh freigeben, besitzende Pixelkopie, BGR ohne extra Vollbildkopie | Diese Kopierpfade waren bereits weitgehend gleich. Gemeinsame Zeit/Weiterverarbeitung verbessert. |
| NDI-Ausgabe | NV12 direkt statt RGB/UYVY; zwei wiederverwendete Puffer, asynchronen SDK-Besitz bis Synchronisation erhalten; Audio unabhängig | Nur Bridge: FastVideo gibt an Browser aus. |
| OMT-Ausgabe | Wiederverwendete NV12-Puffer; direkter VMX-Aufruf mit begrenzter Parallelität, getestete identische HQ-Bitstreams | Nur Bridge: FastVideo hat keinen OMT-Ausgang. |
| Hardwaredecode / SDI-Ausgang | D3D11VA, begrenzte Ausgabearbeit, echte Karten-Completion-Verluste getrennt messen | Nur Bridge. Browser decodieren FastVideo selbst. |

## Bisherige lokale Bridge-Messungen

- Eingangsbildumwandlung: Mittel 3,646 → 1,275 ms; Freigabeverspätung p95 14,987 → 1,988 ms. Kurze stabile OMT/NDI-Fenster ungefähr 50 fps.
- NDI: direkte NV12-Ausgabe senkte den nativen Sendeaufruf p95 in zwei 400-Bilder-Tests von etwa 3,9 auf etwa 1,2 ms.
- OMT/VMX: zwei 200-Bilder-Tests, SDK-p95 3,104/3,018 → VMX-p95 1,827/1,641 ms; Ausgabebytes identisch.
- Status-Bereinigung: 79 Sekunden lokaler SDI-Duplex-Test, etwa 50 fps, keine zusätzlichen beobachteten Verluste im stabilen Fenster.

Diese Werte sind lokale Verarbeitung einzelner Stufen. Sie belegen weder Internetreserve noch Gesamtlatenz oder fehlerfreien Dauerbetrieb. Der frühere SDI-Stundentest hatte echte Karten- und Audio-Verluste; kurze erfolgreiche Nachtests ersetzen keinen neuen Dauertest.

## FastVideo-Abgrenzung

FastVideo behält seine Browserverteilung und seine bisherigen AV1-/H264-/Opus-Einstellungen. Ausgabe bleibt 1280×720 mit der eingestellten Bildrate. Bridge-Funktionen wie WHIP/SRT-Ausgang, WHEP-Empfang, automatische Ausgangsauflösung, H265 und NDI-/OMT-/SDI-Ausgabe werden nicht automatisch Teil von FastVideo. Originale Einstellungen und Userdaten bleiben erhalten.
