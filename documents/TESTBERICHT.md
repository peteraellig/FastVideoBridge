# LAN-Prototyp: Prüfung vom 04.10.2026

Rechner: Intel Core i7-12700K, Intel UHD 770, NVIDIA Quadro RTX 5000.
Der aktuelle Standard ist H.264/NVENC High/P7, 1280×720, maximal 50 fps, Ziel 8 Mbit/s, mit Stereo-Opus. Die folgenden ursprünglichen Messungen bleiben historische Vergleichsdaten. Die damalige Codecpräferenz wurde erst nach setRemoteDescription gesetzt; der tatsächlich verhandelte Codec wurde noch nicht erfasst. Die ursprüngliche Bezeichnung H.264 ist daher für diese Browser-/WHEP-Messungen nicht belegt. Jetzt wird die Präferenz vorher gesetzt und der Browser zeigt den tatsächlich verwendeten Codec.
NDI-Runtime: vorhandene NDI 6 Tools; OMT: offizielles SDK v1.0.0.19.
Python 3.12, aiortc 1.15.0; getesteter In-app-Browser: Chromium 154.

## Gemessen

- Automatische Suche findet NDI und OMT: `PETER-PC (vMix - Output 1)`.
- Der Hardware-NDI-Encoder war laut Benutzer ausgeschaltet; Tests erfolgten
  mit dem Software-Sender auf 192.168.1.102.
- NDI-Rohbildempfang: 1920×1080, 149 dekodierte Bilder in etwa drei Sekunden.
- Ausgang nach Korrektur der Bildratenauswahl: etwa 25 Bilder/s.
- WHEP/H.264-Integration NDI: 100 Frames, 0 unlesbare Messstreifen;
  UTC-Zeitstempel eindeutig und in Reihenfolge. Dekodierter Empfang im
  lokalen Python-Client: Median 63,5 ms (31,2–98,7 ms).
- WHEP/H.264-Integration OMT: 100 Frames, 0 unlesbare Messstreifen;
  UTC-Zeitstempel eindeutig und in Reihenfolge. Dekodierter Empfang im
  lokalen Python-Client: Median 62,9 ms (43,4–86,4 ms).
- Zweiter paralleler Viewer erhält bei beiden Quellen HTTP 409;
  nach DELETE kann eine neue Sitzung aufgebaut werden.
- Browser NDI: beobachteter kurzer Median etwa 61 ms, zunächst 294 gültige
  Bildmessungen und 0 unlesbare Marker.
- Browser OMT: beobachteter kurzer Median etwa 54 ms, zunächst 1410 gültige
  Bildmessungen und 0 unlesbare Marker.
- Browser-Testbild: etwa 50 ms. Separater Stream mit absichtlich 400 ms
  zusätzlichem Puffer: etwa 461 ms; 1199 gemessene Bilder, 0 unlesbare Marker.
  Die Messung erfasst damit die zusätzliche Bildverzögerung. Etwas zusätzliche
  Streuung entsteht durch Bildtakt und Scheduling; die Werte sind keine
  garantierten Grenzwerte.
- Quelle gestoppt: Anzeige meldet Stillstand; beide Latenzfelder zeigen „—“.
- Vier Python-Tests bestanden: H.264-Roundtrip bei stark wechselnden Bilddaten,
  Ablehnung einer Bitbeschädigung, Ablehnung mehrdeutiger Pixel, CRC-Prüfvektor.
- JavaScript-Parser: exakter UTC-Zeitstempel, CRC, beschädigter Marker und Median
  geprüft. Syntaxprüfung der PowerShell-Start-/Setup-/Stop-Skripte bestanden.
- Stop-Skript beendet nur den anhand seiner Projektdatei verifizierten Prozess.
- Doppelstart auf belegtem Port wird vor SDK-Initialisierung abgewiesen.

## Einordnung und offene Praxistests

Die Viewer liefen bei diesen Messungen auf demselben Rechner wie FastVideo;
die NDI-/OMT-Quelle kam über das LAN von einem anderen Rechner. Ein Viewer auf
einem zweiten Rechner ist noch separat zu prüfen, einschließlich dessen
Uhrensynchronisation und Netzwerkpfad. Aktueller Link auf diesem Rechner:
`http://192.168.1.84:8889/viewer`.

Alle Zeiten beginnen am **bereits dekodierten FastVideo-Eingang**, vor Skalierung
und H.264-Encoding. Kamera, Quellencoder und Eingangsdecoding sind nicht enthalten.
Die vom Benutzer angegebenen sieben Verzögerungsframes des OMT-Softwareencoders
liegen davor. Auch die physische Monitorverzögerung ist nicht gemessen.

Die vorhandenen Python-Firewallregeln erlauben bereits eingehendes TCP/UDP;
keine Firewalländerung, Routeränderung oder externe Infrastruktur eingerichtet.
Keine Hardwareencoder-Leistungsmessung, kein Test mit 40 Teilnehmern und kein
Internet-/Audio-Test. FastAudio und das Linux-Kollegenpaket wurden nicht verändert.


## Software-AV1, 04.10.2026

SVT-AV1 4.1 aus PyAV 17.1, Preset 12, pred-struct=1, rtc=1,
lookahead=0, lp=3, rc=2, 2 Mbit/s, 720p25 auf i7-12700K.
100 einfache Testbilder: jedes Eingangsbild sofort codiert, Mittel ca. 4 ms.
Echte Netzwerk-NDI-Quelle: Encoder ca. 7–8 ms/Bild, Browser 25 Bilder/s,
UTC-Median ca. 57 ms auf demselben Rechner (kein zweiter-PC-Test).
Browser bestätigt video/AV1. Einzelne Messstreifen werden nach verlustbehafteter
Kompression unlesbar; CRC verwirft sie, keine erfundene Latenz.

AV1-Tests: RTP-Fragmentierung/Verlust, 60 Bilder mit exaktem Zeitstempel,
angeforderte Keyframes separat decodierbar ohne zusätzliche Bildwarteschlange,
Chrome-Payload 45 mit RTX 46 korrekt verhandelt. H.264/NVENC- und bestehende
Zeitstempeltests ebenfalls bestanden. Gesamter Bildqualitätsvergleich bei
1/2 Mbit/s und 40-Viewer-Lasttest sind noch nicht durchgeführt.

OMT im Browser ebenfalls video/AV1, 25 Bilder/s, UTC-Median ca. 54–56 ms.
Normaler Sender Port 8889 mit NDI/AV1: ca. 59 ms, Encoding 7,2 ms/Bild.
Fünfsekündige Prozessmessung auf 20 logischen Prozessoren: ca. 5,2 % der
gesamten CPU-Kapazität für Empfang, Skalierung, Encoding und WebRTC-Ausgabe.
Das Testmotiv ist keine Garantie für jede Bildkomplexität oder 1080p50.

## Zweiter Rechner / optischer Vergleich

Vom Benutzer gemeldete Originalbild-gegen-Viewer-Messung mit derselben
eingeblendeten Uhr: ca. 200 ms gesamte sichtbare Verzögerung.
Aktuelle Viewer-Telemetrie: UTC-Rohdifferenz 311,05 ms; geschätzter
Viewer-Uhrenvorlauf 238,35 ms; korrigierte Latenz ab FastVideo-Eingang 72,7 ms.
HTTP-Roundtrip 4,6 ms, angezeigte Schätzunsicherheit 4,3 ms.
311 ms sind keine reine Videolatenz. Differenz zur optischen Gesamtmessung
ca. 127 ms liegt außerhalb des gemessenen FastVideo-Abschnitts, einschließlich
früherer Quell-/NDI-Verarbeitung und möglicher Anzeigeunterschiede. Ohne
Zeitstempel am ursprünglichen Quellbild ist deren genaue Aufteilung offen.
Die Viewer-Beschriftung wurde entsprechend präzisiert.


## Stereo-Audio, gemeinsames Encoding und 720p50 / 4 Mbit/s

NDI und OMT mit echtem Programmton geprüft. Gemeinsame Quellzeitcodes,
Opus-Lookahead-PTS, gemeinsame CNAME und RTCP-Senderberichte geprüft.
Interner Blitz/Klick-Test: sieben A/V-Medienzeit-Abstände von -5,66 bis
-7,31 ms. Dies misst die dekodierten Ereignisse anhand der RTCP-Zuordnung;
es ersetzt keine akustische/optische Messung am Lautsprecher und Monitor.
Beschleunigte Simulation von 30 Minuten mit 100 ppm Quelltaktabweichung
bestanden. Ein echter 30-Minuten-Dauertest bleibt ausstehend.

2 und 10 lokale ICE/DTLS/SRTP-Verbindungen: 720p25, 2 Mbit/s bestanden.
10 Viewer in 20 s: mindestens 492 Videobilder und 1002 Audiopakete je Viewer,
insgesamt nur 498 Videoencodes und 1001 Audioencodes. Keine Queue-Drops,
keine Publisherfehler; Encoding 8,98 ms im Mittel. Damit ist gemeinsames
Encoding nachgewiesen, keine zehn unabhängigen Videocodierungen.

40 lokale Verbindungen, 720p50, 4 Mbit/s: Test NICHT bestanden.
Mindestens 957 Videobilder in 30 s (31,9 fps); mindestens 1151 Audiopakete
(38,4 statt 50 Paketen/s). Alle Testempfänger liefen auf demselben Rechner
und entschlüsselten SRTP; keine Videodecodierung. Eine garantierte Leistung
für 40 Zuschauer kann daraus nicht abgeleitet werden. Ein verteilter Lasttest
und Optimierung des Paketversands stehen aus. Bandbreite und Encoderarbeit
allein erklären die Testgrenze nicht.

Aktueller Einzelverbindungs-Test mit Netzwerk-NDI 720p50, AV1 4 Mbit/s und
Stereo-Opus, 30 s: 1405 dekodierte Videobilder, 1549 Audioframes,
2 unlesbare Zeitstempel, Median Eingang-bis-dekodierter-Empfang 60,3 ms
auf demselben Rechner. 60 RTCP-Senderberichte, gemeinsame CNAME,
keine Audio-Zeitcodeunterbrechung, keine fehlenden PCM-Samples,
kein Empfangsfehler. Die Bildrate ist ein Ziel/Maximum, keine Zusicherung
konstanter 50 fps. Browser-Test bestätigte AV1, Opus stereo und Empfang
um 48 fps. Senderstatus nach Schließen zusätzlicher Testserver: 49,1 fps.
15 Python-Tests bestanden. Änderungen am Bildratenlimit verwenden jetzt
Quell-Medienzeit statt schwankender SDK-Ankunftszeit; gleich große
Eingangsbilder werden nicht mehr unnötig skaliert.

Kollegenpaket v1.10.10 read-only geprüft: kompiliertes Go-Programm enthält
Pion WebRTC v4 und gemeinsame Track-Typen, FFmpeg sowie Intel-QSV-/NVENC-
und SVT-AV1-Optionen. Der tatsächlich gewählte Encoder und 100-Zuschauer-
Leistungswerte seines NUC sind aus diesen Dateien nicht belegt. Quellcode
liegt im geprüften opt/whep-gateway-Verzeichnis nicht vor.

## Hardware-Qualitätsstufe H.264, aktueller Stand

Standard jetzt H.264/NVENC 8 Mbit/s, High Profile Level 3.2, P7,
spatial-aq=1, aq-strength=8, multipass=fullres, ull, kein Lookahead,
keine B-Frames. Baseline/Main/High und P4/P5/P6/P7 per CLI wählbar.
Die geerbte aiortc-Bitrate war auf 3 Mbit/s begrenzt; die NVENC-Stufe
respektiert nun die validierten 0,5–10 Mbit/s. Decoder-Test bestätigt
8 Mbit/s Codec-Konfiguration, echtes High Profile, ein Bild pro Eingangsbild.
16 Python-Tests bestanden. H.264-SDP-Level für 720p50 auf 3.2 angepasst.
Browser bestätigt H264/High und 50 empfangene Bilder/s.

100 identische echte OMT-Eingangsbilder bei 720p50:
- Baseline/P4 bisher: 5,71 ms/Bild, 7,21 Mbit/s für den kurzen Ausschnitt.
- Baseline/P7/AQ/Multipass: 7,18 ms/Bild, 7,21 Mbit/s.
- High/P7/AQ/Multipass: 7,46 ms/Bild, 7,20 Mbit/s.
Alle drei ohne zusätzliche Bildwarteschlange. Das Qualitätsurteil für bewegte
ESC-Bilder bleibt Sichtprüfung; die Parameterwahl ist kein Qualitätsnachweis.
PSNR-Y der drei Stufen: 44,03 / 43,24 / 43,84 dB. Spatial AQ verteilt Bits
zugunsten wahrgenommener Details und garantiert keine höhere PSNR.

Benutzer bestätigt direktes OMT-Bild ausreichend scharf, ca. 70 Mbit/s
Eingangsverkehr. OMT bleibt Quelle. Die lokale Rohbildaufnahme belegt keine
massive Unschärfe durch den Empfang; Quelle nicht pauschal als Ursache ansehen.
Linux-Paket enthält Preset-Auswahl P7 (Quality); UI-Voreinstellungen sind
NVENC P4, libsvtav1 Preset 10, libx264 veryfast. Dies sind UI-Voreinstellungen,
keine nachgewiesenen aktiven Parameter des Kollegenrechners.


## Neuer AV1-Vergleich, 4 Mbit/s / OMT

Benutzer beurteilt High/P7-H.264 als fast sendefähig. Erneuter AV1-Vergleich
explizit angefordert. H.264 bleibt gespeicherte Standard-/Referenzstufe.
AV1 nun Preset 8, lp=6, RTC=1, pred-struct=1, Lookahead 0, CBR-Ziel 4 Mbit/s.
100 identische OMT-Bilder: Preset 12/lp3 im Mittel 8,72 ms, Preset 10/lp6
10,46 ms, Preset 8/lp6 12,16 ms. Preset 8 p95 29,68 ms, keine zusätzlichen
Bildwarteschlangen. Kurzzeitbitrate über 2 Sekunden: 5,47 / 4,61 / 4,58 Mbit/s;
4 Mbit/s ist ein Ziel, keine harte momentane Obergrenze. PSNR-Y im selben
Ausschnitt 37,34 / 37,87 / 38,51 dB. Diese Werte ersetzen kein Sichturteil.
VQ/Tune 0 wurde versuchsweise angefordert, SVT warnt aber, dass Low Delay
es auf Tune 1 zurücksetzt. Produktionskonfiguration setzt deshalb Tune 1.

20 s echter AV1+Opus-Decoder-Test: 1027 Video- und 1048 Audioframes,
kein unlesbarer Messstreifen, Median am bereits dekodierten Empfang 65,1 ms,
45 RTCP-Berichte, gemeinsame CNAME, keine Audio-Unterbrechung, keine fehlenden
PCM-Samples, kein Enginefehler. SourceClock/Codec-Pipeline geprüft, keine
Aussage zur physisch sichtbaren Ende-zu-Ende-Latenz. 16 Python-Tests bestanden.
Aktueller Port 8889 läuft mit OMT/AV1/4 Mbit/s/Preset 8; Bildqualität wird
auf dem zweiten Viewer vom Benutzer mit H.264 High/P7 verglichen.

Aktuelle Sichtprüfung auf Benutzerwunsch: AV1 Preset 8 / lp6 bei 6 Mbit/s
statt 4 Mbit/s. Weiter OMT 720p50 mit Stereo-Opus. Server bestätigt
6000000 bit/s Ziel, aktive Videoencodes und ca. 49,5 fps ohne Publisherfehler.
Das ist eine laufende Qualitätsprüfung, kein zusätzlicher Dauertest.


## 2026-10-04: H.264 als Standard und Mehrteilnehmertest

Nach dem visuellen Vergleich bleibt OMT 720p50 / H.264 High / NVENC P7
mit 8 Mbit/s Ziel und Stereo-Opus aktiv. AV1 ist weiterhin optional, aber
für diese Quelle nicht der gewählte Standard.

Reale ICE/DTLS/SRTP-Empfänger auf demselben Rechner, ohne Videodecoding:
- 2 Empfänger / 20 s: mindestens 49,24 Video-Zeitstempel/s,
  49,99 Audiopakete/s, insgesamt 16,02 Mbit/s Nutzlast. Bestanden.
- 10 Empfänger / 20 s: mindestens 48,94 Video-Zeitstempel/s,
  49,99 Audiopakete/s, insgesamt 79,93 Mbit/s Nutzlast. Bestanden.
- 40 Empfänger in einem Prozess / 30 s: mindestens 17,03 Video-
  Zeitstempel/s und 18,27 Audiopakete/s. Echtzeitkriterium nicht bestanden.
- 40 Empfänger in vier Prozessen / 35 s: schlechteste Gruppe mindestens
  11,40 Video-Zeitstempel/s und 13,17 Audiopakete/s. Es treten auch
  serverseitige Audio-/Video-Warteschlangenverluste auf. Nicht bestanden.
  Der 41. Verbindungsversuch wurde korrekt mit HTTP 409 abgelehnt.

Die Zählung erfasst unterschiedliche empfangene RTP-Zeitstempel, nicht
vollständig dekodierte Bilder. Testempfänger und Sender laufen gemeinsam
auf diesem Rechner; CPU-, Scheduling- und Empfangslast lassen sich damit
nicht ausreichend trennen. 40 stabile Zuschauer sind NICHT nachgewiesen.
Bei zehn Empfängern wurden nur 994 Videobilder in 20 s encodiert, nicht
9940. Das gemeinsame Encoding funktioniert. Ein verteilter Lasttest und
gegebenenfalls ein kompilierter WebRTC-Verteiler sind der nächste Schritt.

Viewer-Härtung: Wiederverbindung mit begrenztem Retry-Abstand (1 bis 10 s),
Zeitlimits für WHEP und Sitzungsfreigabe, Erkennung fehlender Videobilder,
Bereinigung der vorherigen PeerConnection. Ein bewusstes Trennen stoppt
Retries; Audio-Stummschaltung bleibt beim automatischen Neuverbinden erhalten.
Quellenneustart mit echtem OMT-Signal geprüft: Viewer kam ohne Reload wieder
mit H.264 High, Opus und 50 fps zurück. 16 Python-Tests und JS-Syntaxprüfung
bestanden. Ein physischer Langzeittest mit Lippen-/Klappensynchronität
und Tests auf mehreren separaten Rechnern bleiben ausstehend.

fastvideo.ch wurde vom Benutzer reserviert und wird voraussichtlich morgen
verfügbar. Heute wurde kein Cloudflare-Tunnel eingerichtet.


## 2026-10-04: Go/Pion-Verteilung umgesetzt

Python empfängt OMT/NDI und encodiert einmal H.264 High/NVENC P7/8 Mbit/s
und Stereo-Opus/128 kbit/s. Go/Pion v4.2.22 übernimmt WHEP, ICE, DTLS/SRTP,
RTP-Senden, NACK und PLI/FIR. Viewerlink bleibt auf Port 8889; Go nutzt UDP
8890. Steuerdienst 127.0.0.1:8890 und Medienbrücke 127.0.0.1:9999 sind lokal.
Die TCP-Medienbrücke übergibt komprimierte Daten, originale PTS und gemeinsame
NTP-Medienzeit. Go schreibt diese in die RTCP-Senderberichte; gemeinsame CNAME.
Begrenzte Sendepuffer pro Viewer, Quellenwechsel beendet alte Verbindungen.
Start-/Stopskripte sind für beide Prozesse angepasst; EXE wird mitgeliefert.

Bildmessstreifen standardmäßig AUS, optional --timestamp-marker.
Viewer überspringt das Pixel-Auslesen und zeigt keine bildbezogene UTC-Latenz.
RTP/RTCP-Zeitstempel für Ton-/Bildsynchronität bleiben vollständig erhalten.

Prüfungen:
- Zwei Go-Unit-Tests für IPC-Framing/Medienzeit und RTCP-Zuordnung bestanden.
  go vet, JS-Syntaxprüfungen und 16 Python-Tests bestanden.
- OMT 720p50, realer H.264/Opus-Decoder, 12 s: 643 Video-/644 Audioframes,
  gemeinsame CNAME, 26 Senderberichte, kein Enginefehler. Fehlende Bildmarker
  sind bei ausgeschaltetem Messstreifen beabsichtigt.
- Internes Flash-/Click-Testbild, 12 s: 298 Video-/596 Audioframes,
  sechs Ereignispaare, Medienzeitversatz -6,5 bis -6,6 ms. Dies prüft
  Quellzeit/Codec/RTCP-Zuordnung, nicht die physische Monitor-/Audioausgabe.
- Python-Empfänger in vier Prozessen, 40 Verbindungen: ca. 43,3 fps,
  Audio ca. 50 Pakete/s, keine Go-Pufferverluste; strenges Kriterium verfehlt.
- Go-Testempfänger, 40 zusätzlich zu zwei Browsern, 20 s: mindestens
  49,8 Video-Markerpakete/s, 50 Audiopakete/s, 323,9 Mbit/s Nutzlast,
  996 Videoencodes insgesamt, keine Go-Sendepufferverluste. Bestanden.
- 100 Gesamtverbindungen, 30 s: 99 Testempfänger plus ein Browser,
  Video worst case 4,43 Markerpakete/s, 7275 Go-Videopufferverluste,
  Audio 50 Pakete/s. Nicht bestanden. Kein Nachweis für 100 stabile Zuschauer.
- Finaler Betrieb mit Obergrenze 40, erneuter 20-s-Test: 40 Testempfänger,
  mindestens 48,45 Video-Markerpakete/s, 50,05 Audiopakete/s,
  315,4 Mbit/s Nutzlast, 969 Videoencodes insgesamt, keine Go-Pufferverluste.
  Zusätzliche Verbindung korrekt mit HTTP 409 abgelehnt. Bestanden.

Alle Lastempfänger laufen auf dem Senderrechner. Die Go-Empfänger zählen
RTP-Markerpakete/Opus-Pakete ohne Decoding. Markerpakete beweisen nicht, dass
jeder mittlere RTP-Fragmentteil vorhanden und jedes Bild decodierbar ist.
Ein verteilter Test mit echten Browsern, eine Langzeitprüfung mit realem Ton
und Internetbetrieb bleiben ausstehend. Standardobergrenze ist 40.
Go-Prozessausfall wird angezeigt; eine automatische Prozessüberwachung
ist noch nicht eingebaut. Heute keine Firewall-/Cloudflare-Änderungen.


## Weiterer Stabilitätstest und Browser-Mehrfachtest

OMT 720p50, H.264 High/P7/8 Mbit/s, Stereo-Opus, Messstreifen aus.
37 Go-Testempfänger liefen 180 Sekunden zusätzlich zu zwei Browsern.
Während der ersten 120 Sekunden lief ein weiterer echter AV-Decoder,
also 40 Gesamtverbindungen; nach dessen Ende 39. Der Ergebniswert
peak_total_viewers=39 ist der Endstand, kein aufgezeichneter Höchststand.
Mindestens 49,98 Video-Markerpakete/s und 50 Audiopakete/s im Gesamtmittel;
schlechtestes 10-s-Fenster 49,90 Video und 49,80 Audio. 8998 Videoencodes,
9000 Audioencodes, keine neuen Go-Pufferverluste, fehlenden PCM-Samples,
Audio-Zeitcode-Unterbrechungen oder Audio-Pufferverluste.

Echter AV-Decoder unter Last: 120 Sekunden, 5994 Video-/5996 Audioframes,
240 RTCP-Senderberichte, gemeinsame CNAME, kein Enginefehler. Ohne
Bildmessstreifen keine UTC-Bildlatenzmessung. Physische Gesamtverzögerung
vom Benutzer weiterhin ca. 200 ms; diese wird als brauchbar akzeptiert.

Neues Browserwerkzeug: /static/loadtest.html. Auswahl 5/10/20/40,
separate WebRTC-Verbindung mit echten Videokacheln je Teilnehmer, Ton stumm,
Decoderbildrate/Paketverluste pro Kachel, Sender-/Empfangspufferwerte.
Einzige Seite reicht; keine Vielzahl einzelner Tabs nötig. Lokale Prüfung
mit 5 Browser-Decodern: alle verbunden, ca. 48 fps, keine gemeldeten
Paketverluste und keine Go-Pufferverluste. Test beenden gibt Sitzungen frei.
20 Kacheln auf dem anderen Rechner stehen als verteilter Test bereit.
Hohe Decoderlast auf dem Testrechner muss separat von Senderlast beurteilt
werden; Fenster während des Tests im Vordergrund lassen.

Empfängerdiagnose mit 40 sichtbaren Browserkacheln (04.10.2026):
Benutzer: i9-10900X / Quadro RTX 4000, vMix parallel, beide Testfenster
sichtbar auf zwei Monitoren. Empfang ca. 390–400 Mbit/s. Screenshots:
alle Empfänger ca. 50 Bilder/s, Decode/Ausgabe teils 19,5–47,5 Bilder/s,
mittlere Decodierzeit einzelner Empfänger bis ca. 187 ms/Bild.
Sender ca. 49,9 Bilder/s; Go-Videopufferverluste 120 als kumulativer
Stand, Audio 0. Engpass in der Verarbeitung am Empfänger wahrscheinlich;
Hardwaredecoder-Nutzung nicht nachgewiesen (Statistik nicht gemeldet).

Vergleichsmodus 'Nur ein Bild anzeigen': fünf lokale Verbindungen,
ca. 40 Mbit/s Video-Nutzdaten, ein sichtbares Bild ca. 49 Bilder/s.
Auch die vier Streams ohne Videoelement werden weiter decodiert
(ca. 49 Bilder/s; Zähler ohne neue Decoderbilder 0/4).
Dieser Modus isoliert daher nur Ausgabe-/Darstellungsarbeit, nicht
Decoderarbeit. Für reine Empfangstests den nativen Go-Test verwenden.
Benutzer testet als Nächstes 20 sichtbare Kacheln auf dem großen Rechner
und 10 auf einem NUC. Ergebnis steht noch aus.

Verteilter Live-Test, 04.10.2026 (Benutzermeldung + Senderstatus):
20 auf i9, 10 auf iPad Pro, 20 auf Gamer-Laptop, 10 auf iPhone;
beide i5/NUCs ausgeschaltet. Benutzer meldet überall 0 Paketverluste,
iPad ca. 1,5 ms/Bild, Laptop 0,9–5 ms/Bild, iPhone 3–7 ms/Bild.
Sender bestätigt 61 aktive Verbindungen (60 Testkanäle plus weitere
Verbindung), 720p50 H.264 High/NVENC P7/8 Mbit/s, gemeinsames Encoding,
49,9 Ausgangsbilder/s, mittlere Encodezeit 13,05 ms, keine Enginefehler.
Go-Videopufferverluste stehen weiterhin kumulativ bei 120, Audio bei 0.
Kein Beleg für 100 Teilnehmer; Dauerprüfung unter 60er-Last noch offen.
Decodierzeiten belegen allein keine Hardwaredecoder-Nutzung und keine
vollständige Bildausgabe; Decode-/Ausgabebildraten getrennt beachten.

Hinweis während des 30-Minuten-Tests: Benutzer meldet stark steigende
Paketverluste auf iPad, iPhone und Gamer-Laptop, alle am gleichen WLAN-AP.
Der Anstieg wurde besonders nach dem Wechsel des iPhones auf diesen AP
beobachtet. Dort laufen zusammen 40 Kanäle, nominal etwa 320 Mbit/s Video
zuzüglich Audio, Protokoll- und Wiederholungsverkehr. WLAN als möglicher
Engpass; Ursache noch nicht mit kabelgebundenem Vergleich nachgewiesen.
Sender zu diesem Zeitpunkt 81 Verbindungen, 49,9–50,3 fps, kumulativ
2015 Go-Videopufferverluste, Audio-Sendepufferverluste 0. Lokale native
Testempfänger ca. 49,8–50 Markerpakete/s. Auf ausdrücklichen Wunsch des
Benutzers unverändert weiterlaufen lassen. WLAN-Paketverluste und
Go-Sendepufferverluste getrennt bewerten; 80 Zuschauer nicht als vollständig
störungsfrei oder als bewiesene doppelte Internetreserve darstellen.
FastVideo – 30-Minuten-Lasttest, 04.10.2026
Zeitraum: ca. 19:36–20:06 Uhr, Europe/Zurich.

179 von 180 Stichproben: 81 aktive Verbindungen.
60 Browserkanäle auf vier Geräten, 20 lokale native SRTP-Testempfänger
ohne Videodecoding und ein zusätzlicher Viewer. Eine Stichprobe zeigte
61 Verbindungen beim regulären Wechsel der lokalen Testprozesse.

OMT 720p50 → H.264 High/NVENC P7, nominal 8 Mbit/s, Stereo-Opus.
Gemeinsames Encoding: einmal encodieren, individuell per SRTP versenden.
Senderbildrate: 49,5–50,3 Bilder/s, Median 49,9. Keine Monitorfehler.
Lokale Testempfänger in der Fortsetzung: schlechtester Durchschnitt
49,998 Video-Markerpakete/s und 50,002 Audiopakete/s;
schlechtestes 10-s-Fenster 49,598 Video / 49,897 Audio.
RTP-Markerpakete beweisen keine vollständig decodierten Browserbilder.

Im 30-Minuten-Messfenster: 7880 zusätzliche Go-Video-Sendepufferverluste,
keine Audio-Sendepufferverluste. Audio-Eingangspufferzähler blieb bei 389,
fehlende Audiosamples bei 1920, keine Audio-Zeitcode-Unterbrechungen.
Die Video-Verluste sind ein aggregierter Zähler über alle Verbindungen,
nicht die Verlustquote eines einzelnen Zuschauers und keine Netzwerk-
Paketverlustmessung. Die konkrete Ursache ist noch nicht nachgewiesen.

Benutzer meldete viele Netzwerk-Paketverluste auf iPad, iPhone und Laptop,
die gemeinsam über denselben WLAN-Accesspoint 40 Streams empfingen.
Am kabelgebundenen Rechner weiterhin 0 Paketverluste und 4–6 ms
Decodierzeit. Das stützt einen WLAN-Engpass, ersetzt aber keinen
kontrollierten Vergleich bei gleicher Last mit kabelgebundenen Empfängern.
Ton vom Benutzer als vorhanden und passend bestätigt. Testkacheln waren
stumm, empfingen aber weiterhin Audio; Lautsprecherausgabe nicht getestet.

Ergebnis: Rund 80 Verbindungen konnten über 30 Minuten gehalten werden,
mit stabiler Senderbildrate und Echtzeitempfang der lokalen Testclients.
Wegen der Video-Sendepufferverluste und WLAN-Paketverluste kein vollständig
störungsfreier 80-Zuschauer-Nachweis und keine bewiesene doppelte Reserve
für 40 Internetzuschauer. Internetbetrieb wurde hier nicht geprüft.

Die 20 lokalen Zusatzempfänger wurden nach Testende automatisch freigegeben.
Danach bestätigt: 61 Verbindungen, Sender weiterhin 49,9 Bilder/s.
Die zeitgesteuerte Überwachung wird nach dem Abschlussbericht pausiert.


FastVideo – Verbindungsdiagnose und Betrieb, 04.10.2026

Aktiver Standard: H.264 High/NVENC P7, 6 Mbit/s, 720p50, Stereo-Opus.
Einmaliges gemeinsames Encoding; normale Zuschauergrenze bleibt 40.

1. Verbindungsdiagnose
Lokale Senderseite zeigt pro anonymer Viewer-ID Nutzdatenrate, getrennte
Video-/Audio-Sendepufferverluste, Browser-/RTCP-Empfangsverluste,
Empfangs-/Decodebildrate, Browserpuffer, Decodezeit, NACK/PLI/FIR-Zähler
und längsten gemessenen WriteRTP-Aufruf. Browser und Testkacheln zeigen
dieselbe kurze ID. Browserwerte werden nach 30 Sekunden als veraltet
behandelt; Sendepufferverluste sind keine Netzwerk-Paketverlustquote.
/api/connections ist nur über lokale Sendersteuerung erreichbar.

2. Langsame Verbindungen
Unabhängige begrenzte Warteschlangen bleiben erhalten. Bei Videoüberlauf
wird veralteter Rückstand verworfen und bis zum nächsten IDR gewartet.
Diese ausgelassenen abhängigen Bilder werden pro Verbindung mitgezählt.
PLI/FIR pro Zuschauer höchstens alle 2 Sekunden; gemeinsame explizite
IDR-Anforderung höchstens einmal pro Sekunde. NACK bleibt aktiv.
Dauerhaft unterbrochene Verbindungen werden nach ca. 20 Sekunden entfernt.
Tests prüfen isolierte Überlaufbehandlung, IDR-Erholung, bounded Audio
und dass eine PLI-Flut nicht andere Zuschauer blockiert.
Kein automatischer Bitratenwechsel und keine zweite Qualitätsstufe.

3. Betrieb
Start-FastVideo startet einen lokalen Supervisor für Python und Go.
Prozessausfälle werden automatisch neu gestartet. Die letzte aktive
Quelle wird nach Neustart wieder verbunden. Pipeline-/Quellfehler
werden mit 10-Sekunden-Abstand erneut versucht. Absichtliches Stoppen
beendet auch die Prozessüberwachung; kein Neustart danach.
Gatewayabsturz und Pythonabsturz wurden praktisch ausgelöst: neue PIDs,
OMT wieder vorhanden, normaler Browser verbunden. Absichtlicher Stop
geprüft: Ports geschlossen und kein Wiederstart. Das Quellen-Retry
wurde mit Fehler/Erholung und absichtlichem Stop als Unit-Test geprüft.
Keine automatische Wiederverbindung der Mehrfachtestseite; normaler
Viewer erneuert seine Verbindung. Keine Überwachung eingefrorener Prozesse.

4. Encoderreserve
Bitrate und P5/P6/P7 sind in der Senderoberfläche wählbar und persistent.
Anwenden verbindet Quelle und Zuschauer neu. Konfiguration und
Wiederverbindung beim Wechsel P7 -> P5 -> P7 über die GUI geprüft.
Encoderdiagnose: Durchschnitt, P95 und Anzahl der letzten bis zu 100
Messungen über dem Bildabstand (20 ms bei 50p).
Gleiche 100 OMT-Bilder bei 6 Mbit/s direkt durch NVENC:
P5 6,77 ms / P95 7,31 ms; P6 6,33 / 7,43; P7 6,24 / 7,04.
Jeweils 5,82 Mbit/s, PSNR Y 43,69 dB, High-Profil, kein gepuffertes Bild.
Kurzer Ausschnitt, kein subjektiver Show-Qualitätsnachweis. Die reine
Codec-Zeit ist nicht die gesamte Encode-/Thread-/Paketisierungsdauer im
Livepublisher (dort aktuell etwa 13–14 ms). Kein Vorteil von P5/P6
nachgewiesen; P7 bleibt aktiv.

Validierung
19 Python-Tests, 4 Go-Tests und go vet bestanden; JS-Syntax geprüft.
Go-Race-Detector konnte ohne CGO/C-Toolchain nicht ausgeführt werden.
Fünf lokale Videokacheln: getrennte Browserdiagnosen sichtbar, keine
Sendepuffer- oder Empfangsverluste; Bildrate im lokalen Test schwankend
(ca. 42–50), deshalb keine feste 50-fps-Zusage für viele Decoder am Sender.

38 native SRTP-Empfänger, ein echter AV-Decoder für 30 Sekunden und ein
Browser: zeitweise 40 Gesamtverbindungen. Native Messung 45 Sekunden:
schlechtester Durchschnitt 49,18 Video-Markerpakete/s, 50,00 Audio;
schlechtestes Zehn-Sekunden-Fenster 48,60 Video / 50,00 Audio.
Keine neuen Go-Video-/Audio-Pufferverluste, keine neuen fehlenden
PCM-Samples, Audio-Zeitcode-Unterbrechungen oder Audio-Pufferverluste.
Echter AV-Decoder: 1414 Videoframes, 1494 Audioframes in 30 Sekunden,
59 RTCP-Berichte, gemeinsame CNAME, kein Enginefehler. Messstreifen aus.
Kurztest, keine Wiederholung des 30-Minuten-/80er-Tests bei 6 Mbit/s.

Server bleibt mit OMT und H.264 High/P7/6 Mbit/s aktiv.
NAS-Projekt und ZIP enthalten die aktualisierten Quellen und Einstellungen.


## Portwechsel 2026-10-05

Standardports jetzt HTTP/WHEP 8879, WebRTC UDP 8880, lokale TCP-Medienbruecke 9999. Zentral in fastvideo.settings.json, inklusive Start/Stop, Supervisor, Go-Keyframe-Rueckkanal und Probe-Defaults. Historische Testadressen oben bleiben unveraendert. 19 Python-Tests, Go-Tests und go vet erfolgreich. Echtzeitprobe: internes 25-fps-Testbild (Netzwerkquelle derzeit nicht auffindbar), 243 dekodierte Videobilder und 496 Audiobloecke in 10 Sekunden, gemeinsamer RTCP-CNAME. Drei weitere lokale SRTP-Empfaenger 5 Sekunden mit 0 neuen Video-/Audio-Gateway-Pufferverlusten. Testbildgenerator meldete fehlende Audiosamples; kein Nachweis eines fehlerfreien Live-Langzeittests. Start/Stop/Neustart auf neuen Ports getestet. Quelle danach gestoppt, Oberflaeche bereit. NAS-Paket aktualisiert. Cloudflare-Webtunnel: http://localhost:8879; UDP-Medienpfad separat direkt oder TURN erforderlich.


## Direkter Internetmodus 2026-10-05

STUN fuer Browser und Go/Pion aktiv: stun:stun.cloudflare.com:3478.
Keine Medienweiterleitung ueber STUN, TURN oder Cloudflare Stream.
/api/ice liefert oeffentliche ICE-Konfiguration ohne Geheimnisse.
Viewer zeigt ausgewaehlten Kandidatenweg direkt/relay und UDP/TCP,
Sender-Verbindungstabelle erhaelt die entsprechenden Messwerte.
STUN-Probe: host-Kandidaten Port 8880, srflx-Kandidaten vorhanden
mit von NAT zugeordnetem Port. LAN-Empfang im Browser direkt/UDP
(host-host). 10-s-Empfang: 250 Video-Frames, 495 Audiobloecke,
gemeinsamer RTCP-CNAME; internes 25p-Testbild, kein Internetnachweis.
19 Python-Tests, Go-Tests und go vet bestanden.
Externes Handy mit WLAN aus zum Test angefragt, Ergebnis noch offen.

Konfiguration: stun_urls als Liste; public_ipv4 leer fuer automatische
STUN-Ermittlung. Fuer feste Routerfreigabe UDP extern 8880 ->
192.168.1.84:8880 und public_ipv4 auf echte oeffentliche IPv4 setzen.
Danach Service komplett neu starten. Go behaelt LAN-Kandidaten,
benutzt bei fester public_ipv4 statt Server-STUN eine Srflx-Abbildung
auf denselben Port. Browser-STUN bleibt aktiv. Dynamische WAN-IP
muss bei Verwendung der festen Abbildung aktuell gehalten werden.
Kein Routerlogin, keine Portfreigabe und kein kostenpflichtiger Dienst
wurden automatisch eingerichtet. CGNAT und UDP-Sperren koennen
weiter einen Direktweg verhindern.


## Korrektur fester WAN-Port und Sender-Toggle 2026-10-05

Die vorherige Srflx-Abbildung erzeugte trotz UDP-Mux separate dynamische
Ports. Nun Host-Address-Rewrite mit APPEND: oeffentliche IPv4 am selben
Mux-Port 8880, LAN-Kandidaten bleiben erhalten. Aktuelle WAN-IPv4 per
Cloudflare Trace ermittelt und als public_ipv4 gespeichert. Externe
Erreichbarkeit nach Korrektur noch vom Benutzer zu bestaetigen.
LAN-Adresse des Senders ist aktuell 192.168.1.104 (zuvor .84).
Routerziel muss UDP 8880 -> 192.168.1.104:8880 sein; DHCP-Reservierung
empfohlen. Weder Routerregel noch DHCP wurden automatisch veraendert.
Bei Wechsel der WAN-Adresse muss public_ipv4 aktualisiert werden.

Sender jetzt ein Start-/Stoppschalter: gruen bei gestoppter Quelle,
rot bei laufender Quelle. Zustand stammt aus Serverstatus, nicht aus
optimistisch angenommenem Klick. Stop und erneuter Start per UI geprueft.
Go-Tests erfolgreich; LAN A/V- und Kandidatenprobe separat dokumentiert.


Viewer-Schalter (2026-10-05): Ein einzelner Schalter ersetzt Verbinden/Trennen. Rot bei aktiver Verbindung bzw. Wiederverbindung, gruen nach manuellem Trennen. Im Browser Trennen und erneutes Verbinden mit Livebild geprueft. Routerziel 192.168.1.104 vom Benutzer bestaetigt; Rechner laut Benutzer statisch adressiert und neu gestartet.

Viewer-Fotouhr (2026-10-05): Klick auf die Verbindungsanzeige rechts oben blendet die lokale Rechneruhr oben links im Videobereich ein; weiterer Klick blendet sie aus. Format HH:MM:SS.d mit Zehntelsekunden, lokale Zeitzone, direkt Date statt Uhrenkorrektur. Browser-Ein-/Ausblenden und laufende Uhr bei Livevideo geprueft. Zehntelanzeige hat 100-ms-Aufloesung und ist kein Nachweis genauer 100-ms-Latenz.

Viewer-Fotouhr auf Wunsch auf HH:MM:SS.dd mit Hundertstelsekunden umgestellt. Aktualisierung per requestAnimationFrame; sichtbare Zeitaufloesung durch Browser-/Displaybildrate begrenzt.

Kompakter Viewer (2026-10-05): Unter dem Video nur Latenz ab FastVideo-Eingang, Puffer, Decoding, WebRTC-RTT und Empfangsbilder/s in einer Metrikreihe; Audio/Opus und Direkt/Relay mit Protokoll darunter. Erklaertexte, Rohdifferenz-/Uhrabgleich-/HTTP-Anzeigen entfernt. Diagnostikmeldungen an Sender unveraendert. Latenz bleibt ohne gueltigen Bildzeitstempel ein Strich. Lokales Livevideo, Opus, aktualisierte Werte und DOM-Referenzen geprueft.

NDI-Audiopegel (2026-10-05): Benutzer meldet -20 dB gegenueber Sendesignal. NDIReceiver gab jedes Audioereignis bisher mit gain=0.1 an Engine._audio; diese feste Absenkung entfernt (gain=1). OMT und Player unveraendert. Achtung: NDI-SMPTE-Float-Referenz kann 20 dB Headroom verlangen (https://docs.ndi.video/all/developing-with-ndi/sdk/frame-types); Unity gilt hier fuer den getesteten vMix-Eingang und ist keine allgemeine Pegelnorm fuer jeden NDI-Sender. Bei einer neuen Hardwarequelle Referenzpegel erneut pruefen. Regressionstest mit kopiertem Stereo-Puffer und Engine-Pegel -6 dBFS statt -26 dBFS; 6 NDI-/Sync-Tests erfolgreich. Dienst neu gestartet, vorhandene NDI-Quelle automatisch wieder aufgenommen. 7.5-s-Statusstichprobe: Video und Audio vorhanden, Spitzen -35.3 bis -12.8 dBFS, keine Engine-/Publisherfehler, ein Viewer verbunden. Stichprobe beweist nicht den Spitzenpegel jedes Programminhalts.

Viewer-Quellenanzeige: Quelle NDI/OMT/Testbild im unteren Infofeld aus api/status.source.kind; Aktualisierung beim Verbindungsaufbau und alle 5 Sekunden bei sichtbarem verbundenem Viewer. Kein veralteter Quelltyp nach Trennen; Fehler unbekannt als Strich. NDI im Browser geprueft.


AV1 B580 / Verbindungsaufbau (2026-10-06): Intel Arc B580 mit Treiber
32.0.101.9034 erkannt. av1_qsv gibt bei 720p50 jedes Eingangsbild ohne
zusaetzliche Encoder-Framewarteschlange aus. 100 Bilder und vier angeforderte
Keyframes jeweils mit frischem Decoder erfolgreich dekodiert.
Go/Pion auf waehlbaren H264-/AV1-Codec erweitert, AV1-RTP-N-Flag in der
IPC-Bridge als Einstiegspunkt uebernommen, RTX/NACK/PLI bleiben aktiv.
Settings dauerhaft av1/qsv/2000000, Portfreigabe und WAN-Adresse unveraendert.
Aktive Netzwerkquelle NDI PETER-PC (vMix - Output 1). WHEP-A/V-Probe:
10 Sekunden, 489 Video- und 495 Audioframes dekodiert, gemeinsamer RTCP-CNAME,
kein Enginefehler; Bildmarker deaktiviert, keine Gesamtlatenzmessung.
Ein Audiozeitcode-Sprung beim Start und 960 fehlende Samples gemeldet;
keine Behauptung einer durchgehend fehlerfreien Audio-Strecke.
Statusstichprobe: 49.5 fps, Encoding Mittel 6.77 ms, p95 7.28 ms.
19 Python-Tests erfolgreich mit zwei Hardware-Skips; gezielter QSV-/Sync-/
Publisher-/Recovery-/NDI-Testlauf 12 Tests erfolgreich. Go tests und vet erfolgreich.
Node-Regression prueft zuerst fehlschlagenden Aufbau mit automatischem Retry,
Cache-Seitenrueckkehr und bewusstes Trennen; spaeter langsamen abgebrochenen
Aufbau, der keine Verbindung mehr erzeugen darf.
Lokaler Browser blieb beim absichtlichen Senderstop offen, ging automatisch
in Retry und zeigte nach Wiederstart wieder AV1-Livevideo ohne Reload.
Neue oeffentliche Viewer-URL auf diesem Rechner direkt beim ersten Oeffnen
verbunden (48-50 fps). Das ist kein unabhaengiger Auslandstest.
Ursache des vom Benutzer beschriebenen ersten fehlgeschlagenen Auslandstarts
nicht abschliessend nachgewiesen; Lifecycle-/Abbruchbehandlung verbessert.
AV1-Kompatibilitaet wird vor Aufbau aus Browsercapabilities geprueft.

AV1-Qualitaetsvergleich (2026-10-06): 100 identische Bilder aus der
laufenden OMT-Quelle auf 1280x720 skaliert, alle Presets mit 50 fps und
2 Mbit/s kodiert; Quelle selbst war 1080p25. Kein eigens aufgelegter
ESC-/Lichteffekt-Stresstest. Medium/Slow/Veryslow: p95 6.94/7.58/9.58 ms,
Luma-PSNR 41.18/41.17/41.23 dB, Bitrate 1.999/1.998/1.999 Mbit/s.
Jede Stufe lieferte ein Paket pro Bild ohne zusaetzliche Framewarteschlange.
Jeweils ein grosser Initialisierungs-Ausreisser ueber 20 ms; unbereinigte
Mittelwerte 14.08/13.77/16.03 ms enthalten diesen. Die minimale PSNR-
Differenz belegt keinen grossen wahrnehmbaren Gewinn auf Lichteffektmaterial.
Medium/Slow/Veryslow im Sender auswaehlbar; Veryslow aktiv, weiterhin 2 Mbit/s.
100 synthetische Frames mit vier frischen Keyframe-Decoderstarts pro Stufe
erfolgreich; 11 Hardware-/Sync-/Publisher-/Recoverytests erfolgreich.
Anwenden in der echten Sender-UI getestet; bestehende IPC-Bridge vor
Neukonfiguration sauber beendet. Aktuelle Quelle OMT K12700 unveraendert.
Erster 5-s-A/V-Probelauf nach Anwenden unterschritt Videoframe-Minimum
(32 Video / 245 Audio); folgender 10-s-Lauf erfolgreich mit 273 Video /
543 Audio, gemeinsamem CNAME und ohne Enginefehler. Audiozeitcode-Spruenge
und fehlende Samples gemeldet; daraus keine fehlerfreie Langzeit-A/V-
Strecke ableiten. Live-Stichprobe anschliessend 25 fps, Encoding Mittel
9.43 ms und p95 10.2 ms. Weitere realistische Effekt- und Netztests erforderlich.
Referenz fuer Presets: https://www.ffmpeg.org/ffmpeg-codecs.html#QSV-Encoders

50p-Ausgang und Audiozeitcode-Meldung (2026-10-06): Benutzer meldete
36 fps und wiederholte Audio-/Videozeitcodefehler. Quelle nun 1080p50.
Veryslow zurueck auf Medium; trotzdem zunaechst 41-50 fps, Encoding
bereits nur 6.7-6.9 ms. Kein eindeutiger Beleg fuer Encoder als alleinige
Ursache. OMT-Stichprobe: Videoabstaende exakt 20 ms, Audio relativ zum
letzten Video 20-40 ms. Empfangspoll von 20 auf 5 ms begrenzt und OpenCV
von 20 auf einen Worker reduziert. Audio-Grenzpruefung vergleicht jetzt
Quellzeit gegen letzten Video-Quellzeitcode, weiter mit 2-s-Grenze; vorher
gegen lokale Laufzeit und ohne gemeinsame Timeline-Phase.
12 Sync-/NDI-/Recovery-/Publisher-Tests erfolgreich. Regression bestaetigt
korrekte gemeinsame Quellzeit auch bei spaeter Anlieferung und lehnt
echte A/V-Abweichung ueber 2 Sekunden weiterhin ab.
20 Statusstichproben ueber 10 Sekunden: Ausgang 49.5-50.3 fps, keine
Engine-/Publisherfehler, keine Quellenwiederherstellung. Folgender realer
WHEP-A/V-Decodetest: 494 Video / 495 Audio in 10 Sekunden, gemeinsamer
CNAME. Audio-Missingcounter blieb waehrend dieses Nachtests unveraendert
bei 15360 (Startphase), zwei Quellzeitcode-Spruenge ebenfalls unveraendert.
Encoding danach 6.82 ms Mittel / 7.37 ms p95. Medium und 2 Mbit/s
bleiben aktiv. Mehrere gleichzeitige Aenderungen: Einzelanteile an der
Bildratenverbesserung nicht isoliert belegt; kein Langzeitnachweis.


720p50 Pipeline-Diagnose (2026-10-06):
Quelle OMT K12700 (vMix - Output 1), 1280x720 / 50p; AV1 Intel QSV,
Medium, 2 Mbit/s. Benutzer meldet stabile 50 fps im Browser, Decode 7-11 ms.
Begrenzte In-Memory-Messung (250 Werte je Stufe) in /api/status ergaenzt:
Empfang inkl. SDK-Wartezeit und nativer Kopie, Aufbereitung, PyAV-Kopie,
Freigabeverspaetung, AV1-Konvertierung/Upload/Encoding und RTP-Paketierung.
Framezaehler trennen Quelle, Freigabe und Encoding; Auswahl-/Deadline-
Drops und VideoTrack-Skips sichtbar. source_timecode_gap_frames ist eine
Schaetzung aus Quellzeitcode-Abstaenden, kein Nachweis verlorener Pakete.
Kein Logging pro Frame. Bridge liest unveraenderlichen Codec einmal pro
Verbindung, statt zweimal pro RTP-Payload den Encoderstatus zu berechnen.
Kein Beleg, dass diese kleine Optimierung frueherer Hauptengpass war.
Keine alten Lasttest-Empfaenger/Probeprozesse gefunden. Die zwei Python-
Prozesspaare sind Windows-venv-Launcher plus eigentlicher Prozess.

61 Statusstichproben ueber 61.01 s nach Startphase:
Quelle 50.023, Freigabe 50.023, Encoder 50.039 Bilder/s (Zaehlerdifferenzen,
kleine Randabweichung wegen asynchroner Stufen). Ausgangs-Fenster 49.9-50.3.
KEINE Zunahme von Quellzeitcode-Gaps, Auswahl-/Deadline-Drops, Track-Skips,
Audio-Unterlaeufen/-Spruengen/-Bufferdrops, Publisher-/Gateway-Bufferdrops
oder Quellenwiederherstellungen. Startwerte dennoch nicht null: drei Track-
Skips, 35 geschaetzte Quellzeitcode-Gapframes, ein Audio-Sprung, 5760 fehlende
Samples. Diese entstanden vor der Messbaseline und blieben unveraendert.
Am Ende: Aufbereitung 0.572 ms, PyAV-Kopie 1.289 ms, Codec einschliesslich
Konvertierung/Upload 6.536 ms, AV1-Paketierung 0.029 ms (je Mittel letzter
250 Werte). Encoding inkl. Executor ca. 6.88 ms; maximaler beobachteter
p95 7.76 ms. Konvertierung und Hardwareencode nicht separat isoliert.
Freigabeverspaetung gegen gemeinsame Medien-Deadline: Mittel 7.611,
p95 14.567 ms; hier kleines weiteres Schedulingpotential, kein FPS-Einbruch.
Python-App nutzte 52.50 Prozent EINES logischen Kerns, Gateway 1.72,
Supervisor 0.03; keine CPU-Gesamtauslastung von 52.50 Prozent behaupten.
Nur OMT-Log wuchs um 2188 Bytes (mDNS unerreichbarer Socket, entfernt),
keine laufende Logflut. Browsermeldung zuletzt 50 fps, 0 Paketverluste,
0 verworfene Frames; 1 realer Viewer, keine zusaetzlichen Empfaenger.
Aktuelle Messung reproduziert keinen Engpass. Fruehere A/V-Quellzeitcode-
Fehlermeldung mit Wiederherstellung vor diesem Neustart in Log vorhanden;
die kurze fehlerfreie Messung beweist keine langfristige Fehlerfreiheit.
Messdateien: work/pipeline-720p50.json, work/pipeline-720p50-summary.json.
Messloop nach 61 Stichproben beendet; Diagnose sammelt nur begrenzte
Werte im Speicher weiter. Unittests: 20 gelaufen, 2 Hardwaretests uebersprungen.


Viewer ohne leere Latenzanzeige (2026-10-06):
Gesamt-Latenzkachel wird ausgeblendet, solange keine gueltige bildbezogene
Messung mit Zeitmarker vorliegt. Keine Ersatzberechnung aus RTT/Decode;
Puffer, Decoding, WebRTC RTT und Empfang verbleiben im adaptiven Grid.
Cacheversion viewer.js/style.css latency2. JavaScript-Syntax und bestehende
Reconnect-Regression erfolgreich. Echter Chromium-Viewer lokal mit AV1,
50 fps und nur vier sichtbaren Kacheln bestaetigt. Kein Senderneustart.
Apple bestaetigt AV1-Hardwaredecoder im iPhone 15 Pro, WebKit Safari 18.0
nennt Reparatur von AV1 WebRTC HW decoding auf diesem Modell. Aktuelles
Problem noch nicht zugeordnet: iOS-Version/Browser und konkretes Symptom
vom Benutzer angefragt; kein iPhone-Test auf diesem Rechner moeglich.
Referenzen: https://www.apple.com/newsroom/2023/09/apple-unveils-iphone-15-pro-and-iphone-15-pro-max/
https://webkit.org/blog/15865/webkit-features-in-safari-18-0/

Nachtrag: Benutzer bestaetigt iPhone 15 Pro / Safari mit einwandfreier
AV1-Wiedergabe. Keine Safari-Stoerung zu beheben. Viewer prueft bereits
RTCRtpReceiver.getCapabilities(video) auf den Sendercodec. Chrome-Symptom
noch ohne exakten Meldungstext; keine pauschale iPhone-Inkompatibilitaet.


Paralleltest AV1 + H.264 Quick Sync (2026-10-06):
Benutzer beauftragt zuerst Hardwareencoding parallel zu AV1 und Pruefung,
ob AV1-Bildrate darunter leidet. H264-QSV-Adapter in fastvideo/h264_qsv.py
vorbereitet; noch NICHT im produktiven Encoderfactory/Gateway verdrahtet.
H.264 High, 720p50, Ziel 6 Mbit/s, Medium, kein Lookahead/B-Framebuffer,
async_depth 1, LowDelayBRC und erzwungene IDRs. Hardwaretest mit 100 Bildern
und vier frischen Decoderstarts bestanden; jedes Bild ohne zusaetzliche
Framequeue ausgegeben, High-Profil und RTP-Zeitstempel verifiziert.
Live-Test ohne Senderneustart: laufender AV1-QSV-Stream unveraendert,
voruebergehend separater H264-QSV-Prozess mit zweitem OMT-Empfaenger.
Damit zusaetzliche Empfangs-/Kopierlast enthalten. Spaetere Architektur
soll einmal empfangen und die Frames an beide Encoder verteilen.
Windows GPU-Zaehler: AV1-App PID16464 und H264-Test PID17068 auf derselben
Adapter-LUID 0x00000000_0x00010B57. AV1-Encoder laeuft auf der B580;
H264-QSV in diesem Test ebenfalls auf diesem Adapter, nicht auf UHD770.
Deviceauswahl noch implizit; diese Beobachtung ist keine feste Bindung
fuer beliebige andere Rechner/Adapterreihenfolgen.
H264: 3211 Bilder, 64.156 s nach erstem Bild, 50.034 Bilder/s.
Payload-Bitrate 5.741 Mbit/s, Ziel 6 Mbit/s. Encoding Mittel 7.635 ms,
p95 10.722 ms, Maximum 13.985 ms nach Ausschluss erster 100 Warmupframes;
keines davon ueber 20 ms. Erstinitialisierung 817.087 ms separat.
AV1: vor Parallelbetrieb 49.899, waehrend 50.006, danach 50.014 encodierte
Bilder/s. Ausgangsfenster in allen Phasen 49.9-50.3. Median gemeldeter
Encoding-Mittelwerte davor 7.875, waehrend 6.830, danach 6.770 ms.
Maximaler gemeldeter p95 vorher 12.40, waehrend 12.30, danach 7.52 ms.
Keine kausale Beschleunigung/Verlangsamung daraus ableiten: Zeitwerte
schwankten bereits vor Parallelbetrieb; echtes wechselndes Videomaterial.
Ueber gesamtes Messfenster keine Zunahme von Track-Skips, Auswahl-/Deadline-
Drops, Audio-Unterlaeufen/-Spruengen/-Pufferdrops, Publisher-/Gateway-
Bufferdrops oder Quellenwiederherstellungen. Keine Engine-/Publisherfehler.
Browserdiagnose in /api/status stammt jeweils vom zuletzt meldenden Viewer,
kann zwischen Clients wechseln; kein eindeutiger kontinuierlicher Nachweis
konstanter Browserlatenz oder aller Client-Dropcounter. Gemeldeter Puffer
Median vorher ca.32 ms, waehrend/danach ca.52 ms; Kausalitaet nicht belegt.
Ergebnis bestaetigt parallele Hardwareencoder bei 50p in diesem Kurztest;
noch keine End-to-End-H264-Fallbackverbindung/Audiosync/40-Viewerreserve
geprueft. H264-Test nach 65 s automatisch beendet; Monitor ebenfalls beendet,
AV1-Einstellungen und laufender Sender unveraendert.
Hardware-H264-Test erfolgreich; Standardsuite 21 Tests, 3 uebersprungen.
Messdateien: work/h264-parallel-result.json, av1-h264-parallel-status.json,
h264-parallel-summary.json und h264-parallel-gpu-proof.json.
FFmpeg-QSV-Optionen: https://www.ffmpeg.org/ffmpeg-codecs.html#QSV-Encoders


Automatischer AV1/H264-Fallback und Client-ID (2026-10-06):
Produktiver Go-Gateway und Python-Publisher auf zwei Videoformate erweitert.
Ein Quellempfang, zwei unabhaengige Hardwareencoder; gemeinsame Quellzeit,
eine Opus-Encodingpipeline. AV1 2 Mbit/s, H264 Intel QSV Baseline 4 Mbit/s
auf Benutzerwunsch (nicht die 6 Mbit/s des vorherigen Hardwarebenchmarks).
Baseline fuer breite H264-Kompatibilitaet statt urspruenglichem High-Test.
FV01 Header kind2 bedeutet H264-Fallbackvideo, kind0 Hauptvideo, kind1
geteiltes Audio. Pion waehlt/routet Codec pro Session, insgesamt max40.
Client und Gateway werten reale SDP-Videoangebote aus; AV1 bevorzugt,
H264 falls AV1 nicht angeboten. Expliziter Vergleich /viewer?codec=h264.
HTTP-Proxy gibt Codec-Query und Codecantwortheader weiter. UI-Badge zeigt
wirklich empfangenen Codec; Session-ID achtstellig als dezente 10px hellgraue
Browser-Einblendung unten rechts. Verbindungen mit vollem UUID/Codec in
Gateway-Log protokolliert; /api/connections enthaelt Codec. Noch keine
Laender-/Personenzuordnung; Browser-Einblendung kein eingebranntes Wasserzeichen.

Integrationstest fand Initialisierungsblock von ca.1.4s und Quellzeitfehler:
Encoder nun nacheinander mit internem Bild initialisiert, bevor Quellempfang
seine Uhr setzt. Kontexte werden behalten; internes Bild nie ausgesendet.
Zeitcodeschutz nicht abgeschwaecht. Empfangspoll 1 statt5ms; bei identischem
720p-Raster kein erneutes Nullbild/Kopieren. Gemeinsame BGR->NV12-Konvertierung
mit einem wiederverwendeten PyAV-Reformatter, threads=1. Automatische
Workerzahl fuehrte in Zwischenstand zu ca.12ms Konvertierung und deutlichen
Track-Skips; dieser Zwischenstand ersetzt. Eigene NV12-Planes/Metadaten
pro Encoder gegen parallele pict_type-/Keyframe-Rennen getestet.

Finale parallele WHEP-Decodetests nach Anlaufphase, je15.000s:
AV1: 748 Video / 750 Audio, 49.87 Bilder/s; H264: 747 Video / 750 Audio,
49.80 Bilder/s. Beide gemeinsame CNAME, je32 RTCP-Berichte, kein Enginefehler,
Audio-Missingcounter0, Quell-Audio-Spruenge0. Programmquelle verwendet,
kein Blitz/Klick-Lipsyncnachweis; keine Bildzeitmarker/Gesamtlatzenz gemessen.
25 Statusstichproben waehrend Nachtest: Ausgang49.5-50.3; Zunahme2 Deadline-
Drops und4 aggregierte Track-Skips. Keine Audio-/Publisher-/Gatewayqueue-
Verluste oder Quellenwiederherstellung. Daher keine perfekte Frameverlust-
freiheit behaupten. Encoding zuletzt AV1 5.52ms / H264 5.73ms Mittel,
H264-Target4Mbit, H264-Audioencodes0 (gemeinsamer Opus vom Hauptpublisher).
Echte Browser lokal und oeffentlicher H264-Link auf diesem Rechner jeweils
Bild, Codec-Badge, Client-ID und 50fps verifiziert. Kein neuer externer
Mobilnetz-/Auslandstest. Automatische Auswahl mit AV1/H264-Angeboten sowie
Audioangebot ohne Video getestet; Gatewaytest verhindert Codecmischung
und verifiziert kind2-Quellzeit. Publisherregression20+20 Empfaenger ergibt
nur einen Encode pro Variante. Standardsuite24 Tests,4 uebersprungen;
separate Hardwaretests High6Mbit und Baseline4Mbit erfolgreich. Go Tests,
Vet, JS-Auswahl und Reconnect-Regressions erfolgreich. Nicht benoetigte
Audio-Aufgabe im H264-Videopublisher entfällt. Testempfaenger beendet;
normaler Sender laeuft mit beiden Varianten. Sicherung auf Z:\Fastvideo.
Messdateien: work/dual-av1-av-final.json, dual-h264-av-final.json,
dual-pipeline-final.json und dual-pipeline-summary.json.


## 2026-10-06: Wiederverbindung auf iPhone / iOS

Benutzer meldet nach zweimal Trennen/Starten schwarzes Bild bei Safari/AV1 und Chrome/H264; Status Verbunden, Bildstatistik nur Striche. Senderstatus waehrend Untersuchung: beide Encoder aktiv, rund 50 fps, kein Pipelinefehler. Ursache auf dem Mobilgeraet noch nicht abschliessend bestaetigt.

Viewer bereinigt beim Trennen jetzt Remote-Tracks, Eventhandler und Videoelement mit pause/srcObject=null/load. Neue Verbindung startet stumm; Wiedergabe wird bei Track, geladenen Metadaten, verbundenem Peer und abgeschlossenem SDP angestossen. NotAllowedError versucht erneut stumm. Framecallbacks alter Peers koennen nicht die neue Verbindung aktualisieren. Cacheversion reconnect2. Diagnostik erfasst framesDecoded/framesReceived, paused/readyState/muted und Alter des letzten ausgegebenen Bilds.

JavaScript-Reconnect-Regression inkl. unmuted-Abbau, Autoplay-Ablehnung und veralteter Framecallback bestanden; Codecwahl-Test bestanden. Python: 24 Tests, 4 hardwarebedingte Skips. Browser-Tests ueber oeffentlichen HTTPS-Link im lokalen In-App-Browser: AV1 und H264 nach Trennen/Starten wieder mit Bild und rund 50 fps; zusaetzlicher Zyklus geprueft. Kein Ersatz fuer erneuten Test auf iPhone ueber Mobilfunk. Bitraten und Encoderparameter unveraendert.

Nachtrag reconnect3: iPhone-Verbindung 539ba37d zeigte framesDecoded=522, fps=50, packetsLost=0, aber playbackPaused=1, readyState=0 und kein ausgegebenes Bild. Damit ist fuer diesen Versuch Medienempfang und Decoding nachgewiesen; das Problem liegt zwischen MediaStream und Videoelement. Viewer haengt keinen leeren Stream mehr vor SDP an, sondern verwendet direkt den befuellten ontrack.streams[0]; streamlose Tracks erzeugen ebenfalls zuerst einen befuellten Stream. Play-Fehlername wird fuer weitere Diagnose angezeigt. Regression fuer direktes Anhaengen des Remote-Streams bestanden. Mobilgeraet-Bestaetigung ausstehend.

Nachtrag reconnect4 auf Benutzerwunsch: nach verbundenem Peer einmalig nach 4 Sekunden ohne ausgegebenes Bild automatisch disconnect(false)/connect(false). Laufendes Bild, versteckte Seite, manueller Stop und veralteter Peer verhindern diesen Start-Neuversuch. Automatischer Neustart setzt die Einmal-Sperre nicht zurueck; erneuter manueller Start erlaubt wieder einen Versuch. Bestehende allgemeine Ausfall-Wiederverbindung bleibt bestehen. JS-Regression prueft schwarzen Start, Einmaligkeit und ununterbrochene laufende Wiedergabe; bestanden.

Nachtrag reconnect5: Benutzer meldet NotAllowedError auch nach neuem Anhaengen des Streams. Expliziter Knopf Wiedergabe starten erscheint im Bild bei Wiedergabesperre. Sein Click ruft play synchron vor jedem await auf (muted Bildstart); Tonknopf kann ebenfalls direkt freigeben. Solange diese Browsersperre aktiv ist, kein 4-Sekunden-Startneuversuch und kein periodischer Bild-Watchdog-Neustart. Alte Play-Promises sind versuchsgebunden. Regression prueft Sperre auch bei stummem Start, direktes play im Click, Aufheben der Sperre und kein nutzloses Reconnect; bestanden. iPhone-Bestaetigung ausstehend.

2026-10-06 Pufferdiagnose Client 41991ccd (iPhone 5G, Ton an): AV1 Decoding ca. 3.3-3.5 ms. Acht Stichproben nach RTT-Spitze 181 ms zeigen RTT 18-48 ms, weiterhin nur 5 kumulative Paketverluste, Videopuffer 935-1023 ms und zuletzt 17 fps / 247 kumulative Frame-Drops. Sender rund 50 fps, keine Gateway-Queueverluste. Ursache nicht allein aus RTT/Paketverlust ableitbar. Viewer buffer1 setzt per Feature-Detection jitterBufferTarget=50 ms und ggf. playoutDelayHint=0.05 Sekunden identisch fuer beide Receiver; Browser darf Ziel uebersteuern. Diagnostik erfasst tatsaechliche API-Unterstuetzung sowie Audio-/Video-Ziel- und Mindestpuffer und Audio-Ausgleichssamples. Reconnect-Tests inkl. fehlender/ablehnender Setter bestanden. Sender einmal fuer neue Diagnostik neu gestartet. Mobilgeraet-Ergebnis noch ausstehend.

2026-10-06 Benutzer bestaetigt reproduzierbar: stumm einwandfrei, Ton an fuehrt zu schnell wachsendem Puffer; iPhone-Lautsprecher, kein Bluetooth. Sender-Audio-PTS/RTCP unveraendert und bestehende Quellzeitcode-Tests intakt. Gezielter Versuch fuer iOS-Audioausgabe: direkt beim Wiedergabe-/Tonklick AudioContext(latencyHint=interactive, sampleRate=48000) mit stummem Oszillator/Gain=0 aktivieren. Programmaudio bleibt nativ im gemeinsamen Videoelement, nicht in einer getrennten Web-Audio-Pipeline. Ressourcen beim Disconnect schliessen. Historische WebKit-Berichte 231110/236363 dokumentieren kleinere Ausgabehardware-Puffer durch aktiven AudioContext; damalige Bugs wurden gefixt, dies beweist keine aktuelle Ursache. Wirkung auf Benutzergeraet muss bestaetigt werden. Diagnose audio_output_primed/base_latency/output_latency.

Auf Benutzerwunsch zusaetzliche Bandbreite in Mbit/s: Differenz der inbound RTP bytesReceived fuer Video+Audio pro stats-Intervall, keine Schaetzung der Anschlusskapazitaet und kein kompletter IP/SRTP/RTCP-Overhead. Neue Verbindung/Counterreset invalidiert Baseline. Reconnect-Regression inklusive AudioContext-Lifecycle und Bandbreitenberechnung bestanden. Cacheversion bandwidth1; Backend einmal neu gestartet.

2026-10-06 Sender-Statusanzeige: Benutzer meldet Sender nicht erreichbar / Failed to fetch, bestaetigt laufende Uebertragung. API running=true, 50.3 fps, drei Viewer, keine Source-Recoveries oder Audio-Discontinuities. Frontend hielt einmalige Fetch-Fehler dauerhaft im gemeinsamen Meldungsfeld. Sender status2 entfernt jetzt eigene alte Poll-/Quellfehler bei erfolgreicher gesunder Abfrage und bewahrt unabhängige Aktionsfehler. Polls ueberlappen nicht, Fetch mit no-store und 8-s-Timeout. Regression prueft Wiedererreichbarkeit, unabhaengige Aktionsfehler, behobene Sourcefehler und Poll-Sperre; bestanden. Kein Serverneustart und keine Encoder-/Streamaenderung.

2026-10-06 Sender Tabellen / Systemwerte: Auf Wunsch alle Betriebs-, Encoder- und Diagnosewerte in gut lesbaren Tabellen ueber den Zuschauer-Links angeordnet, lange Kleinschrift-Zusammenfassung entfernt. CPU gesamter Rechner aus GetSystemTimes (Kernelzeit inkl. Idle korrekt verrechnet), physischer RAM benutzt/gesamt aus GlobalMemoryStatusEx, Messcache 1 s ohne wartende CPU-Messung. Lokaler /api/system-Endpunkt geschuetzt; fremder Host wurde mit 403 abgewiesen. Startzeit in Europe/Zurich, Uptime ab Python-Serverstart. Gesamte Viewer-Bandbreite aus Differenz Video+Audio-Nutzdaten pro verbundenem Viewer, ohne Netzwerkoverhead; Codeczaehler fuer aktive verbundene Peers. Neue Verbindung braucht erste Messbasis. Keine neue Bibliothek noetig. Python 26 Tests / 4 Skips, Sender-JS inkl. gemischter Codecs und Datenratensumme bestanden. Anwendung einmal neu gestartet. Browser zeigt 3/40, 2 AV1 / 1 H264, CPU 8.5 %, RAM 10.0/31.8 GiB und 7.62 Mbit/s; dies sind Stichproben, keine feste Auslastung.

Nachtrag Sender-Kacheln: Auf neue Benutzerpraeferenz Betriebs-/Diagnosewerte in einzeln abgerundete Kacheln umgestellt, hellblaue Raender und blaues Fuellfeld, Beschriftung 12 px und Wert 15 px. Vier Spalten am Desktop / zwei auf schmalen Bildschirmen. Viewer-Verbindungstabelle weiterhin tabellarisch mit gut sichtbarem Container. IDs und Messlogik unveraendert, ohne weiteren Serverneustart. Screenshot visuell geprueft.


## 2026-10-06: Getrennte Encoder-Einstellungen und Viewer-Browser

AV1 und H.264-Ersatzstream haben eigene Bitraten und Intel-Presets (Medium/Slow/Veryslow), eigene Anwenden-Buttons und persistente Einstellungen. Anwenden startet die gemeinsame Pipeline neu; beide Stream-Einstellungen bleiben getrennt erhalten. H.264 bleibt Baseline. Standardwerte unveraendert: AV1 2 Mbit/s, H.264 4 Mbit/s, jeweils Medium.

Browser und gemeldete Version werden aus dem User-Agent der bestehenden Viewer-Diagnose ermittelt und pro Verbindung angezeigt. Vollstaendige Versionsnummern sind nur sichtbar, soweit der Browser sie meldet. Live nach Neustart: 3 Viewer (2 AV1, 1 H.264), ca. 50 Bilder/s, kein Publisherfehler. Chrome und Safari samt Version sichtbar. Automatische Tests fuer getrennte Aenderung/Persistenz, ungueltige Werte ohne Pipeline-Stopp und Browser-Erkennung bestanden; Sender-JavaScript-Pruefungen bestanden. NAS-Kopie aktualisiert.


## 2026-10-06: Bildzeitstempel fuer Fotovergleich reaktiviert

Persistente Einstellung timestamp_marker=true. Framegebundener UTC-Millisekundenmarker mit CRC, im Viewer verdeckt. Hardwaretest: jeweils 20 bewegte Rauschbilder bei 720p50 mit AV1 QSV 2 Mbit/s und H.264 QSV Baseline 4 Mbit/s; alle Zeitstempel nach Decoding exakt gelesen. Bestehende Timestamp-, Timing- und Wiederverbindungstests bestanden.

Die Viewer-Latenz zeigt jetzt den direkten UTC-Wert bei synchronisierten Rechneruhren, ohne HTTP-Uhrenabgleich. Die abgeleitete Uhrenkorrektur bleibt getrennt in der Senderdiagnose. Messstrecke: SDK-Bildeingang FastVideo bis geschaetzter Browser-Bildausgabezeit; Zuspieler/NDI/OMT vor FastVideo und physische Displaylatenz sind nicht vollstaendig enthalten. Fotovergleich Original/Viewer deshalb nicht dieselbe Messstrecke.


Ergaenzung: iOS-Geraete sind nicht gezielt synchronisierbar. iPhone/iPad verwenden deshalb die getrennt beschriftete Anzeige Latenz (geschaetzt) mit HTTP-Uhrenabgleich; Tooltip mit Unsicherheitswert. Synchronisierte PCs behalten direkten UTC-Wert. Negative rohe UTC-Differenzen auf iOS verhindern die korrigierte Messung nicht. Netzweg-Asymmetrie kann die Schaetzung verzerren; kein Ersatz fuer Fotovergleich.


## 2026-10-06: Fotoabgleich der Zeitmessung

Benutzerfoto: Sender 32.70, iPad 32.63 (70 ms, Anzeige 94 ms), PC 32.46 (240 ms, Anzeige 584 ms). Nicht dieselbe Messstrecke wie Marker ab SDK-Eingang. Live-Diagnose danach: AV1/Chrome Rohwert ca. 602 ms, korrigiert ca. 134 ms, geschaetzter Viewer-Uhrenvorsprung ca. 468 ms bei ca. 33 ms Unsicherheit. Snapshot nicht zeitgleich mit Foto, daher keine exakte Rueckrechnung.

Viewer-Anzeige auf aktuellen Frame statt Sekundenmedian umgestellt. PC wechselt bei deutlichem Uhrenversatz (groesser als geschaetzte Unsicherheit + 20 ms) ebenfalls zur expliziten Schaetzung. Negative Rohdifferenzen werden fuer moegliche Korrektur zugelassen. Dies behebt die irrefuehrende Darstellung des Rohwertes bei ungleichen Uhren, beweist aber keine Uebereinstimmung mit dem vollstaendigen Foto-Messweg. Encoder-/Transportparameter unveraendert.

## 2026-10-07: Senderoberflaeche mit Seitenleiste

Sender nutzt die volle verfuegbare Fensterbreite. Permanente linke Navigation: Dashboard, Connections, Diagnostics, Setup. Seitenleiste zeigt Eingangs-Audiopeak mit Pegelbalken, Quelle, Signalstatus, Ausgangsbildrate sowie System-CPU, RAM, Serverstart, Uptime und summierte Viewerbandbreite. Dashboard enthaelt Start/Stopp, Betriebskacheln und Viewerlinks; Setup enthaelt Quellenwahl, direkten OMT-Eingang und getrennte AV1/H.264-Einstellungen. SDI2OMT bleibt ein eigenstaendiges externes Projekt.

Browserpruefung: alle vier Abschnitte erreichbar; Setup zeigt AV1 2 Mbit/s und H.264 4 Mbit/s, jeweils Medium. Desktop ohne horizontalen Seitenueberlauf und ohne doppelte Element-IDs. JavaScript-Syntax und bestehende Sender-Status-Recovery-Tests bestanden. Gestoppter Sender blieb gestoppt; keine Encoder-, Empfangs- oder Transportparameter geaendert. Ansicht bei schmalen Fenstern ordnet Navigation und Status oberhalb der Kacheln an.

Nachtrag: Diagnose-Kacheln ins Dashboard verschoben. Viewer links ersetzt Diagnostics als dritten Tab (/sender#links). Beide Ansichten im Browser geprueft; kein Pipeline-Neustart erforderlich.

Nachtrag: Distribution und Process supervision aus dem Dashboard entfernt und in neuem englischem Help-Tab dokumentiert. Encoding time zeigt P95 der letzten bis zu 100 Messungen, Mittelwert im Tooltip. Framebudget-Ampel: gruen ohne Ueberschreitung, gelb bei einzelnen Ueberschreitungen, rot wenn P95 selbst ueber Budget liegt; ohne Messungen neutraler Strich. Keine Aenderung der Encoderparameter. Help und Dashboard im Browser geprueft; Sender-Syntax und Status-Recovery-Tests bestanden.

Nachtrag: Audioformat aus dem Dashboard entfernt. Im Setup unter der Quellenwahl als gemeinsame Audioinformation fuer AV1/H.264 angezeigt; Format unveraendert Opus Stereo / 48 kHz / 128 kbit/s. Darstellung im Browser geprueft.

Nachtrag: System details aus Setup entfernt. Dashboard-Codec-Kacheln enthalten kleinere Zusatzinfos: AV1-Qualitaet, gemeinsamer Timestamp und Arbeitsverzeichnis; H.264-Qualitaet und Profil. Browserdarstellung und Sender-Status-Pruefung bestanden.

Korrektur: Timestamp-Status und Arbeitsverzeichnis stehen als Info unter der Eingangsquellenwahl in Setup. Dashboard-Codec-Kacheln behalten nur Qualitaet und H.264-Profil. Browserdarstellung geprueft.

2026-10-07: Kleine Live-Eingangsvorschau im Pegelcontainer (240x135 JPEG, maximal 5 Bilder/s, nur bei sichtbarer Senderseite). Ein bereits vorhandenes Bild wird ausserhalb des Empfangslocks in einem Worker verkleinert; kein zusaetzlicher Quellenempfang oder WebRTC-Viewer. Gemeinsamer Cache begrenzt JPEG-Erzeugung auch bei mehreren Senderseiten. Ohne Signal verschwindet das alte Bild. Endpoint nur lokal mit bestehender Sender-Zugriffspruefung. Tests fuer konkurrierende Anfragen, Ratebegrenzung, Bildverlust und unveraenderten Eingang bestanden. Live: OMT K12700 (vMix - Output 1), 30 JPEG-Anfragen HTTP 200, mittlere Resize/JPEG-Arbeit 2.799 ms (Maximum 16.922 ms), 5754 Bytes/Bild, ca. 0.23 Mbit/s bei 5 fps ohne HTTP-Overhead. Ausgangssnapshot 49.1 fps, Encoding Mittel 5.34 ms/P95 6.36 ms, 0/100 ueber 20 ms, keine Pipelinefehler. Kein kontrollierter A/B-Latenzvergleich. SDI-OMT Quelle PETER-PC entdeckt, aber bei diesem Test ohne Bilder.

Vorschau: 80-ms-Ueberblendung ueber zwei vorab dekodierte JPEG-Bildschichten; Bildfrequenz und Backend unveraendert. Browserpruefung zeigt beide Schichten geladen und aktive 0.08-s-Opacity-Transition. Reduzierte Bewegungen respektiert. Kein Serverneustart erforderlich.

2026-10-07: Untersuchung der Bildratenanzeige. output_fps misst Freigaben der letzten maximal 100 Bilder nach lokaler Zeit, nicht die nominale Quellenrate. 20-s-Beobachtung: Eingang 49.99 fps, Freigabe 49.49 fps, zehn frame_deadline_drops; keine Quellzeitcode-Luecken. Erster A/B/A-Test je ca. 12 s: Vorschau an 48.67 fps/17 Freigabe-Drops, aus 49.80/3, an 49.62/5. Kein stabiler Zwei-fps-Effekt, moeglicher Zusatzaufwand. Vorschau-Resize von INTER_AREA auf bilineare Stichproben umgestellt. Danach A/B/A: an 49.49 fps/5 Drops, aus 49.39/6, an 49.64/4; Eingang weiterhin ca. 50 fps. Resize/JPEG-Messwerte 0.24-0.55 ms statt vorher im Mittel 2.799 ms. Kurze Abschnitte, keine statistische Lastgarantie. Die verbleibenden Freigabe-Drops stammen aus verspaeteter Bereitstellung/Windows-Scheduling; keine genaue Unterstufe als alleinige Ursache nachgewiesen. Anzeige nicht auf nominale 50 maskiert. Live input preview kann in Setup ohne Stream-Neustart pausiert werden; gilt fuer laufenden Server, Neustart aktiviert Vorschau wieder. Tests fuer ausgeschaltete Vorschau, Cache, Eingangsunveraendertheit und Status bestanden. Vorschau nach Tests wieder an, OMT K12700 aktiv. Messdateien work/preview-fps-ab-test.json und work/preview-fps-ab-optimized.json.

2026-10-07: Eingangspfad und Bildfreigabe korrigiert. OMT-Receiver zeigt getrennte begrenzte Messreihen fuer SDK-Warten und Audio-/Videokopie unter receiver_timings. Vorher: 16.05 s, Eingang 803, Freigabe 791, elf deadline_drops, zehn track_skips; BGRA-BGR-Kopie Mittel 3.302 ms/P95 5.879 ms. NumPy-Kopie von strided RGB-Scheiben ersetzt durch pixelgleiche cv2.cvtColor BGRA2BGR, einschliesslich Zeilenpadding und eigener Speicherhaltung getestet. Faellige Bilder werden vor SDK-Warten und direkt nach dessen Rueckkehr freigegeben, bevor Audio-/Bildaufbereitung weiterarbeitet; vorher nur am Ende des Schleifendurchlaufs. Backlog-Schutz bleibt fuer echte Rueckstaende erhalten. Zwischenmessung: 24.09 s, 1205 Eingangs-/1204 freigegebene Bilder, null deadline_drops, aber noch 16 track_skips fuer beide Encoder zusammen. VideoTrack weckt jetzt beide Encoder ueber eine thread-sichere gemeinsame asyncio-Benachrichtigung statt 4-ms-Polling. Clear vor Sequenzpruefung verhindert verlorene Benachrichtigungen; Stop/Fehler wecken wartende Verbraucher. Keine neue Quellverbindung, kein zusaetzlicher Puffer, kein geaenderter gemeinsamer RTP/RTCP-Zeitbezug.

Abschlusstest mit einem echten lokalen AV1-Browser: 30.11 s, Eingang 50.02 fps, Freigabe 49.99 fps, AV1-Encoding 50.02 fps; null deadline_drops, null track_skips, null Quellzeitcode-Luecken, null fehlende Audiosamples/Audio-Pufferverluste. BGRA-BGR Mittel 0.687 ms/P95 1.019 ms. Browser 50 fps, null Paketverluste und null browserseitig verworfene Bilder; Ton empfangen, Wiedergabe stumm. Keine Aussage ueber hoehere Last oder dauerhaft exakt null Timing-Jitter: release_lateness weiterhin Mittel 7.808 ms/P95 14.773 ms, aber in diesem Abschnitt ohne verworfene Bilder. Input-release, Doppelkonsumenten-/Stop-Benachrichtigung, Sync, Timestamp und Vorschautests bestanden. Messdateien work/input-release-before.json, input-release-after.json, input-release-notified.json.

2026-10-07: Quellenname und Signalstatus gemeinsam gruen bei laufender Quelle mit Signal, rot bei No signal/Stopped oder nicht erreichbarem Status. Orange Problemmeldung mittig in der Stream-Leiste an der vom Benutzer markierten Stelle. Meldungen fuer fehlendes Video mit Quellenname, Source/Encoder/Gateway-Fehler, AV1/H.264-P95 oberhalb des Framebudgets oder >=5 Prozent ueber Budget sowie neu hinzugekommene Input-Video-/Audio-Pufferverluste. Historische Zaehler allein erzeugen keinen dauerhaften Alarm. Schwellen in Help beschrieben. Tests fuer beide Statusfarben, fehlendes Signal, beide Encoderalarme, Verlustdifferenzen und Wiederherstellung bestanden; echte Browserdarstellung gruen geprueft. Keine Pipeline-Aenderung und kein Neustart.

Quellenname und Signalstatus jetzt zusaetzlich dauerhaft mittig in der Stream-Leiste: gruen bei Signal present, rot bei No signal/Stopped/Status unavailable. Orange Problemmeldungen stehen bei Bedarf direkt darunter im gleichen Balken. Browserdarstellung und Tests fuer beide Farben/Quellenangabe bestanden.

Layoutdetail: Titel und Quellen-/Alarmanzeige auf gemeinsame Mittelachse der Hauptflaeche ausgerichtet. Desktop per symmetrischem Grid, schmale Ansicht mit mittiger Statuszeile. Browsermessung: Differenz der Mittelachsen 0 Pixel.

## 2026-10-07 — Pipeline-Absicherung und kompakter Viewer

- Empfang, gemeinsame Bildaufbereitung, Audio, Publisher, lokale Bridge, Go-Verteilung und Browser-Start/Wiederverbindung geprüft. Codec, Bitraten und gemeinsamer Audio-/Videozeitbezug bleiben unverändert.
- Publisher stoppt jetzt erst nach Abschluss einer bereits laufenden nativen Audio-/Video-Encoderarbeit. asyncio-Cancellation allein beendet einen to_thread-Auftrag nicht; ein Neustart soll sich nicht mit alter Encoderarbeit überschneiden.
- Eine erzwungene Encoder-Ausgabe ohne Nutzdaten lässt die Schlüsselbildanforderung bestehen. Damit warten neue Abonnenten nicht auf eine bereits verbrauchte Anforderung.
- Python- und Go-Zuschauerpuffer nutzen das aktuelle Schlüsselbild zur Wiederherstellung nach Überlauf. Ist damit die Warteschlange repariert, wird kein zusätzliches gemeinsames Schlüsselbild angefordert.
- Gezielte Regressionstests für Stoppen während nativer Arbeit, leere Schlüsselbildausgabe sowie Pufferüberlauf auf einem Schlüsselbild hinzugefügt. Python: 43 Tests, 5 übersprungen, übrige bestanden. Go-Suite und alle sechs JavaScript-Tests bestanden.
- Live-Kontrolle nach Neustart: 20 s, 1000 ausgegebene Bilder (50 fps), keine zusätzlichen Deadline-/Track-/Audio-/Gateway-Pufferverluste und keine zusätzlich fehlenden Audiosamples. AV1 Encoding p95 6,39 ms, H264 7,25 ms. Bereits beim Anlauf gezählte 960 fehlende Audiosamples bleiben im Zähler sichtbar. Kurze LAN-Prüfung; keine neue Last- oder Internetreserve nachgewiesen.
- Viewer: Buttons rechts neben dem Logo, Codec AV1/H264 in der unteren Infozeile. Leere Meldungszeile belegt keinen Platz. Verbunden-Anzeige bleibt als Schalter für die Viewer-Uhr erhalten. Mobile Kopfzeile darf umbrechen.

## 2026-10-07 — Viewer-Titel und sichtbarer Verbindungsstatus

- Beschriftung Viewer unter dem Logo entfernt. Im Setup steht Viewer title mit Save title; Titel wird in fastvideo.settings.json gespeichert und oben im freien Bereich zwischen Logo und Buttons angezeigt. Standard: FastVideo Live. Leerer Titel blendet den Text aus. Speichern verändert Encoder und laufende Verbindungen nicht.
- Lokaler POST /api/viewer/display prüft Titeltyp, 80-Zeichen-Grenze und Steuerzeichen; bestehende Encoderkonfiguration bleibt erhalten. Titel wird nur als textContent dargestellt.
- Erste Statistik-Kachel zeigt AV1/H264 groß und darunter den Verbindungsstatus. Grün nur bei verbundenem Peer mit einem innerhalb der letzten 1,5 s tatsächlich ausgegebenen Bild; ohne neues Bild steht Kein Bild rot. Nicht verbunden/Verbinde ebenfalls rot. Klick auf diesen Status schaltet weiterhin die hundertstelgenaue Viewer-Uhr.
- Titeländerungen erreichen laufende Viewer über die bestehende Statusabfrage (bis 5 s). Kopfzeile bricht bei schmalen Anzeigen um.
- Zwei Python-Tests für Titelvalidierung und Persistenz ohne Encoderänderung sowie JavaScript-Test für tatsächlichen Bildstatus bestanden. Bestehende sechs JavaScript-Suiten bestanden. Live AV1: 50 Bilder/s; Trennen und Wiederverbinden geprüft, getrennten Status als rot bestätigt.

## 2026-10-07 — Zweizeiliger Viewer-Titel

- Das erste Semikolon im gespeicherten Viewer-Titel trennt erste und zweite Zeile. Leerzeichen an der Trennstelle werden entfernt; Ausgabe bleibt sicherer Klartext (textContent und white-space: pre-line).
- Setup zeigt neben Viewer title den Hinweis (Text after ; appears on the second line). Bestehende Titel ohne Semikolon bleiben einzeilig.
- Browser-Prüfung mit dem vorhandenen Titel Choose any title you like ; for the viewers bestätigt den Umbruch. Kein Senderneustart erforderlich. Reconnect- und Signalstatus-JavaScript-Prüfungen bestanden.

## 2026-10-07 — Viewer vollständig auf Englisch

- Viewer-Dokument auf lang=en umgestellt. Buttons, Statuskachel, Statistikfelder, Audioinformationen, Quelle/Transport, Viewer-Uhr, Copyright-Zeile sowie Tooltip-/ARIA-Texte auf Englisch.
- Dynamische Texte für Wiederverbindung, Zeitmessung, fehlendes Bild, Codec-Unterstützung, Autoplay und Audiozustand ebenfalls übersetzt. Benutzerdefinierter Titel bleibt erhalten, einschließlich Semikolon-Zeilenumbruch.
- WHEP-Fehler werden im Viewer nach HTTP-Status mit englischen Meldungen angezeigt; technische Serverantwort bleibt für Diagnose in der Konsole. Native Browserfehler werden mit englischer Beschreibung und Fehlername dargestellt.
- Alle sieben JavaScript-Prüfungen bestanden (Codec-Auswahl, Latenz, Senderstatus, Timestamp, Timing, Wiederverbindung und tatsächlicher Bildstatus). Kein Serverneustart erforderlich.

## 2026-10-07 — CPU- und GPU-Modell in der Systemkachel

- CPU-Modell einmal beim Serverstart aus der Windows-Hardware-Registry gelesen. GPU-Adapter einmal über EnumDisplayDevicesW abgefragt, doppelte Monitorausgänge entfernt; aktiver primärer Display-Adapter bevorzugt. Erfassung hier insgesamt ca. 0,68 ms.
- Hardwarebezeichnungen werden in SystemMetrics gespeichert. /api/system liefert diese gespeicherten Daten zusätzlich zur bestehenden CPU-/RAM-Messung; keine zusätzlichen Hardwareabfragen während Statuspolling oder Encoding.
- System CPU zeigt Modell und bestehende Host-Auslastung; darunter System GPU mit Intel Arc B580 Graphics. Tooltip nennt weitere erkannte Adapter (hier Intel UHD Graphics 770). Keine GPU-Prozentangabe, weil diese eine separate Messung benötigt und nicht bereits verfügbar ist.
- Drei SystemMetrics-Tests bestanden, einschließlich einmaliger Hardwareerfassung trotz mehrerer ungecachter Statusabfragen. Senderstatus-JavaScript-Test bestanden. Live-Oberfläche und laufendes 720p50-Signal geprüft.

## 2026-10-07 — Version 0.0.2.0: persistentes Warnungslog und internes Testbild

- Sender- und Viewer-Versionsanzeige, README und Übergabe auf 0.0.2.0 gesetzt. Bestehende Anwendungserkennung der Start-/Stop-Skripte bleibt kompatibel.
- Server prüft dieselben Warnbedingungen alle 1,5 s unabhängig von geöffneten Senderseiten. Der Status liefert die ermittelte Warnungszeile, sodass Anzeige und Log denselben Zustand verwenden. Warnungen werden je Ursache getrennt mit Beginn/Ende aufgezeichnet; wechselnde P95-Zahlen erzeugen keine neuen Ereignisse derselben laufenden Warnung.
- Rotierendes Log C:\Fastvideo\work\warnings.jsonl (1 MB + 2 Backups). UI zeigt die letzten 200 Ereignisse, aktuell aktive Warnungen orange, mit lokaler Startzeit, Dauer und Status. Nach normalem Stopp Stopped; nach abruptem Abbruch Interrupted mit unbekannter Dauer statt erfundener Endzeit. View log / Hide log und Refresh unten im Setup, Aktualisierung alle 5 s nur bei geöffnetem Log.
- Browserseitige Meldung Sender unreachable wird nach wieder erreichbarem Server nachgetragen, sofern die Senderseite noch geöffnet ist. Früher aufgetretene Warnungen vor Einführung dieses Logs lassen sich nicht nachträglich vollständig rekonstruieren.
- Beim ersten Startprüfungslauf Namenskonflikt mit Pythons warnings-Modul erkannt; Modul nach warning_history.py umbenannt. Überwachten Windows-Start danach erfolgreich geprüft.
- Fünf neue Warnungslog-Tests für Dauer/Persistenz, schwankende Werte ohne Duplikate, getrennte Ursachen, Zählerreset, Abbruch und Begrenzung bestanden. Insgesamt 20 gezielte Python-Tests sowie alle sieben JavaScript-Prüfungen bestanden.
- Internes Testbild verwendet das bestehende PNG-Logo oben links, darunter aktuelles Datum mit englischem Wochentag und 24-h-Uhr einschließlich Hundertsteln. Logo und Hintergrund einmal vorbereitet, Datum nur bei Tageswechsel neu gezeichnet. Bestehender 25-fps-Testgenerator und Blitz-/Klick-Abgleich bleiben erhalten. Nutzer hat während der Prüfung auf das interne Testbild geschaltet; live im Viewer geprüft.

## 2026-10-07 — Testbild mittig angeordnet

- Auf Benutzerwunsch Logo, Datum und 24-h-Uhr als zentrierte Gruppe in den vorhandenen schwarzen Testblock verschoben. Linker Farbbalken bleibt frei; Datum und Uhr werden nach gemessener Textbreite horizontal zentriert.
- Beim synchronen 2-s-Blitz leuchtet der äußere Blockrand weiß; Logo und Zeit bleiben auf schwarzem Grund lesbar. Der gemeinsame Blitz-/Klick-Zeitbezug bleibt erhalten.
- Version weiterhin 0.0.2.0; Neustart mit zuvor vom Benutzer gewähltem Testbild erfolgreich.

## 2026-10-07 — Quellenwahl mit Set source bestätigen

- Setup: Set source neben Find sources, Status Confirmed bzw. Selection not applied. Dropdown-Änderung allein schaltet nicht um. Dashboard-Start verwendet die bestätigte Quelle, unabhängig von einer unbestätigten Dropdown-Auswahl. Direkte OMT-Adresse wird über Set OMT auf dieselbe Weise bestätigt.
- Stabile Quellenidentität (kind/address) statt Listenindex; globale Neusuche/Reihenfolge kann die ausgewählte Quelle nicht mehr vertauschen. Legacy-Start mit index bleibt für bestehende Aufrufer kompatibel.
- Bestätigte Auswahl wird atomar in work/selected-source.json gespeichert. Stoppen entfernt sie nicht. Beim Set im gestoppten Zustand startet die Quelle erst über Dashboard; im laufenden Zustand wird umgeschaltet. Fehlgeschlagener Wechsel bestätigt die neue Quelle nicht. Wiederholtes Set der bereits laufenden Quelle löst keinen erneuten Neustart aus.
- Aktiv bzw. bestätigt gewählte Quelle bleibt auch bei Stopped in der Anzeige sichtbar. Quellenfehler haben eigene englische Meldungen; native Fehler werden im Serverlog erfasst, statt jeden Fehler als Invalid settings or source request zu verdecken.
- Vier Python-Regressionstests (Set ohne Start, Live-Set, veränderte Suchreihenfolge, fehlgeschlagener Wechsel) sowie bestehende Wiederverbindungs-/Senderprüfungen bestanden. Live geprüft: Testquelle gestoppt bestätigen, Dashboard starten, OMT nur auswählen (Test bleibt aktiv), Set source drücken (OMT läuft wieder mit ca. 50 fps). Neustart übernimmt bestätigte OMT-Quelle.
- Version weiterhin 0.0.2.0.

## Viewer logo (2026-10-07)
Viewer logo reduced by 20% (176px desktop / 152px mobile). Setup Hide logo saves immediately and persists viewer_hide_logo; hidden logo is replaced by small fastvideo.ch text. Connected viewers adopt changes through existing status polling without an encoder restart. Both states verified live; existing JavaScript checks pass.

## Load test page (2026-10-07)
Public /static/loadtest.html?count=20 provides selectable 1-40 real browser connections as video tiles. Updated to current codec negotiation (Auto/AV1/H264) and sender ICE configuration. Muted stereo audio included. Receiver fps, decode time and packet loss plus aggregate sender output and queue drops are displayed.

## Runtime viewer capacity (2026-10-07)
Setup maximum viewers 1-80 with Set limit; initially raised to 60. Local-only control updates Go capacity under its viewer lock and persists settings without reconnecting existing clients. Lowering the cap retains existing viewers and blocks new admissions until below the cap. Supervisor reads saved capacity when relaunching gateway. Load-test count accepts up to 80. Go tests and API boundary/persistence tests pass. Installing the runtime control required one server restart.

## Users and personal viewer links (2026-10-07)
Users tab: create/edit/save/delete name/email, enabled state, 1/2 connection limit, copy/replace personal link. Persistent atomic user/users.json; tokens random 256-bit keys; local-only admin endpoints. Server-side serialized admission uses authoritative gateway owner metadata, including after app restart; same-tab reconnect replaces the old session. General access optional, Personal links only restricts new admissions. Live test: one accepted/second rejected, limit changed to two with original uninterrupted, both then connected at 50 fps; same-tab reload worked; replacing key closed both sessions; anonymous offer rejected 403 in personal-only mode. Test user removed, general mode restored. Python regression tests and Go tests passed; existing JS tests passed. Runtime user database excluded from generic NAS/ZIP package.

## Organiser disconnect controls (2026-10-07)
Connections shows personal user name or General viewer and a Disconnect action per session. Users and Connections also provide Disconnect general viewers; it closes only sessions without a personal user. Admin disconnection returns 410 to viewer diagnostics and a state check before automatic reconnect; official viewer stays stopped until explicit Connect/reload. Personal links only blocks later anonymous admissions. Live bulk test closed the agent-created general viewer while the existing named personal session remained connected; viewer displayed organiser-disconnected message.
