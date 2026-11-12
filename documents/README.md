# FastVideo 0.0.2.0

Stand 07.10.2026: Intel Arc B580, AV1 mit 2 Mbit/s und H264-QSV-Fallback mit 4 Mbit/s, gemeinsamer Stereo-Opus-Ton. Sender und Viewer zeigen Version 0.0.2.0. Setup → Warning log → View log zeigt die letzten 200 Warnungsereignisse mit Zeitpunkt, Dauer und Status. Die serverseitige Prüfung läuft auch ohne offene Senderseite; Beginn und Ende werden in work/warnings.jsonl gespeichert (rotierende Dateien, insgesamt ca. 3 MB).

Das interne Testbild enthält mittig im schwarzen Feld das FastVideo-Logo, aktuellen englischen Wochentag und Datum sowie lokale Senderzeit im 24-Stunden-Format mit Hundertsteln. Sein bestehender Blitz-/Klick-Test und seine bisherige Bildrate bleiben erhalten.

Eine NDI- oder OMT-Quelle auf Windows empfangen und als H.264- oder AV1-Video per
WebRTC/WHEP an Browser im gleichen Netzwerk senden. Standard: 1280×720,
maximal 50 ausgegebene Bilder/s, H.264 mit 8 Mbit/s und Stereo-Opus.
Auf diesem Rechner: Quadro NVENC, High Profile, P7, räumliches AQ,
Fullres-Multipass, ohne B-Frames oder Lookahead. OMT ist die aktuelle Quelle.
Python empfängt die Quelle und encodiert einmal. Go/Pion übernimmt WebRTC
und verteilt dieselben Pakete an bis zu 40 Verbindungen. 40 verschlüsselte
Testempfänger bestanden einen Kurztest mit 49,8 fps; getrennte Browser und
Langzeitbetrieb bleiben zu prüfen. Der Bildmessstreifen ist standardmäßig aus.

## Ablage und Start

- Lokaler Arbeits- und Laufzeitordner auf diesem Rechner: `C:\Fastvideo`.
- NAS-Kopie für Austausch/Verteilung: `Z:\Fastvideo`. Nicht vom NAS starten.
- `Start-FastVideo.cmd` öffnet die lokale Sendersteuerung und startet den
  Server im Hintergrund, wenn er noch nicht läuft.
- Quelle auswählen, **Übertragung starten**, Zuschauer-Link kopieren.
- Der zweite Rechner öffnet den Link und benötigt nur Chrome oder Edge.
- Im Viewer **Ton einschalten** anklicken. Der automatische Start ist stumm.
- Die Sendersteuerung ist nur über `http://127.0.0.1:8879/sender` auf dem
  Senderrechner erreichbar. Viewer: `http://<LAN-IP>:8879/viewer`.
- **Stoppen** in der Oberfläche stoppt das Video. `Stop-FastVideo.cmd`
  beendet zusätzlich Python und das zugehörige Go-Gateway.
- Bei Quellenwechsel verbindet sich der Viewer automatisch neu.
- Es gibt keine Cloudverbindung, keine externen ICE-Server und keine CDN-Dateien.

## Voraussetzungen auf einem weiteren Windows-Rechner

1. Projekt lokal nach `C:\Fastvideo` kopieren.
2. Python 3.12, 64 Bit installieren und `Setup-FastVideo.cmd` ausführen.
3. Für NDI: eine lizenzierte offizielle NDI-Laufzeit/NDI Tools installieren.
   Die vorhandene DLL wird geladen, keine NDI-Binärdatei wird mitverteilt.
   Optional `FASTVIDEO_NDI_DLL` auf den vollständigen DLL-Pfad setzen.
4. OMT: native Bibliotheken v1.0.0.19 liegen unter `vendor\omt`, zusammen
   mit MIT-Lizenz und dem C-Header. Sie werden nur innerhalb dieses Projekts
   verwendet. Projektinterner OMT-Zustand liegt unter `work\omt-state`.
5. Eingehendes TCP für die Seite (8879) und UDP für WebRTC müssen im LAN
   erlaubt sein. Das Go-Gateway verwendet UDP 8880. Keine Router-Portfreigabe
   notwendig. Kein Gast-WLAN mit gegenseitiger Client-Isolation verwenden.
6. Windows muss TCP 8879 für die Webseite und UDP 8880 für das Go-Gateway
   im privaten LAN zulassen. Heute wurden keine Firewall-Regeln verändert.

Das interne Testbild braucht weder NDI noch OMT. Für OMT gibt es alternativ
eine direkte Adresseingabe im Format `omt://192.168.1.102:6400`.

## Optionaler Messstreifen (standardmäßig deaktiviert)

Mit `python app.py --gateway --timestamp-marker` kann der Bildmessstreifen
für Diagnose wieder eingeschaltet werden. Ohne ihn bleiben bildbezogene
Latenzwerte leer; Empfangspuffer, Decoding und RTP-Medienzeit für AV-Synchronität
bleiben vorhanden. Die folgende Beschreibung gilt nur bei eingeschaltetem Streifen.

## Was die Latenzanzeige bei aktiviertem Streifen misst

**Dekodierter FastVideo-Eingang → geschätzte Browser-Bildausgabe.**

Direkt nach Rückgabe eines dekodierten Frames aus der NDI-/OMT-Bibliothek
wird die Sender-UTC-Zeit in Millisekunden erfasst. Vor Skalierung und Videoencoding
wird diese Zeit als binärer Messstreifen in die oberen 16 Bildzeilen geschrieben.
Der Streifen umfasst eine Kennung (16 Bit), Unix-Zeit (48 Bit) und CRC16
(16 Bit). Jedes Bit belegt einen 16×16-Block, passend zum H.264-Makroblockraster.
Der Messstreifen ersetzt diese oberen Bildzeilen; er kann im Viewer ausgeblendet
werden, bleibt aber zur Messung im codierten Bild.

Der Viewer liest ihn in `requestVideoFrameCallback` aus dem aktuellen Videobild
und subtrahiert ihn von `expectedDisplayTime`, umgerechnet auf seine UTC-Uhr.
Das ist bildbezogen und berücksichtigt Skalierung, Videoencoding,
WebRTC-Übertragung, Jitterpuffer, Decoding und Browser-Präsentation.
Es ist **keine Kamera-bis-Monitor-Messung**: Verzögerung vor dem dekodierten
FastVideo-Eingang und physische Monitorverzögerung sind nicht enthalten.
Die sieben Eingangsframes des OMT-Softwareencoders liegen vor dem Messpunkt.
Sie entsprechen bei 25 fps 280 ms, bei 50 fps 140 ms; die echte Quellbildrate
und die Position des ursprünglichen Messpunkts sind hierfür entscheidend.

Voraussetzung: beide Systemuhren sind synchronisiert. Deren Restabweichung
geht direkt als Messfehler ein. Der Browser-Ausgabezeitpunkt ist eine Schätzung.
Kein Anspruch auf Genauigkeit einer externen optischen Messung.

- Median der letzten Sekunde plus letzter einzelner Bildwert.
- Prüfsummenfehler/mehrdeutige Pixel werden verworfen, nicht geraten.
- Fehlende frische Messung, Hintergrund-Tab und Stillstand zeigen keinen
  alten Latenzwert als aktuell. Nach Uhrensprung muss neu verbunden werden.
- Der Messstreifen ist eine verlässliche Prototyp-Lösung; native RTP-Metadaten
  ohne sichtbaren Streifen können später ergänzt und gegen ihn geprüft werden.

## Grenzen dieses ersten Prototyps

- Standard: H.264, 720p50, Ziel 8 Mbit/s. NVENC wird automatisch gewählt, sofern verfügbar. Die CPU-Ausweichstufe ist weiterhin auf 3 Mbit/s begrenzt und keine gleichwertige Qualitätsstufe.
- `app.py --codec h264 --encoder nvenc --h264-profile high --nvenc-preset p7 --bitrate 8000000` erzwingt den getesteten Hardwaremodus.
- Ältere Viewer können `--h264-profile baseline` benötigen. Kein unterschiedliches Profil je Viewer: alle erhalten denselben einmal codierten Stream. Ein nicht unterstütztes Profil wird bei der Verbindung abgelehnt.
- AV1-Vergleich: `app.py --codec av1 --bitrate 4000000 --av1-preset 8 --av1-lp 6` nutzt SVT-AV1 4.1 mit aufwendigerer Qualitätsstufe. Der aktuelle laufende Vergleich verwendet diese Einstellung; der Standard beim nächsten normalen Start bleibt H.264 High/P7/8 Mbit/s.
- AV1-Preset 8–13 und Parallelität 1–6 sind per CLI wählbar. Niedrigere Preset-Zahlen rechnen aufwendiger. Tune 0 wird vom SVT-Low-Delay-Modus auf Tune 1 zurückgesetzt; deshalb ist Tune 1 ausdrücklich gesetzt. Kein Lookahead, weiterhin RTC/Low Delay.
- AV1 ist in WebRTC optional. Aktuelles Chrome/Edge/Firefox testen; nicht aus AV1-Dateiwiedergabe auf WebRTC-Unterstützung schließen.
- Der AV1-Test nutzt eine feste LAN-Bitrate. Internetbetrieb braucht zusätzlich eine geprüfte Staukontrolle.
- Gemeinsames Encoding ist eingebaut. 2 und 10 Verbindungen mit 720p25/2 Mbit/s bestanden; der lokale 40-Verbindungen-Test mit 720p50/4 Mbit/s bestand die Echtzeitgrenzen nicht.
- Kein Talkback, Aufnahme, persönlicher Zugang oder Internetbetrieb.
- Kein Login für Viewer: gedacht für das vertrauenswürdige lokale Testnetz.
- Software-Gateway des Kollegen bleibt unverändert und wird nicht gestartet.
- Sourceverlust friert das letzte Bild ein; der Viewer meldet den Stillstand.
- Nicht alle NDI-/OMT-Quellvarianten und Browser wurden bereits getestet.

## Prüfungen und Diagnose

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node tests\test_timing.js
# Quelle starten, dann Audio/Video prüfen:
.\.venv\Scripts\python.exe -m tests.probe_av --base http://127.0.0.1:8879 --seconds 30
# Lasttest auf separatem Server, dort alle anderen Viewer trennen:
.\.venv\Scripts\python.exe -m tests.probe_load --base http://127.0.0.1:8891 --viewers 10 --seconds 20
# Separater Messaufbau mit absichtlich 400 ms zusätzlichem Bildpuffer:
.\.venv\Scripts\python.exe app.py --port 8879 --codec h264 --fps 25 --max-viewers 1 --test-delay-ms 400
```

Logs: `work\fastvideo-8879.log`, `work\server-error.log`, `work\omt-<Prozess-ID>.log`.
Die NAS-Kopie enthält keine virtuelle Umgebung, Logs oder lokalen Betriebszustand.

## Offizielle Grundlagen

- NDI-Empfang: https://docs.ndi.video/all/developing-with-ndi/sdk/ndi-recv
- OMT C-Bibliothek: https://github.com/openmediatransport/libomt
- OMT-Binärpaket: https://github.com/openmediatransport/libomtnet/releases/tag/v1.0.0.19
- aiortc: https://aiortc.readthedocs.io/en/latest/api.html
- Browser-Frame-Callback: https://developer.mozilla.org/en-US/docs/Web/API/HTMLVideoElement/requestVideoFrameCallback

## AV1, Diagnose und spätere Verteilung

Software-AV1 ist vorhanden; kein GPU-Wechsel für diesen 720p50-Test nötig.
Adapter in fastvideo/av1.py ergänzt aiortc 1.15 um AV1-OBU-Paketierung,
Codec-Verhandlung und den erweiterten dynamischen RTP-Bereich (Chrome PT 45).
Codecpräferenz wird vor setRemoteDescription angewandt. AV1-SDP-Level ist bei 720p50 4.0.
SVT liefert pro Eingangsbild genau ein Paket mit dem codierten Bild. Angeforderte
Keyframes werden getestet; RTP-Pakete werden bei 1200 Bytes fragmentiert.

Die Latenzdiagnose zeigt unveränderte UTC-Messung, zusätzlich geschätzten
Uhrenversatz mit Unsicherheit, WebRTC-Roundtrip, Intervallwerte für Jitterpuffer
und Decoding. Sie ändert keine Rechneruhr. Nicht lesbare Messstreifen werden
weiterhin per CRC abgewiesen. Einzelne AV1-Bilder können keinen gültigen
Messwert liefern; nur frische gültige Bilder gehen in den Median ein.

40 Viewer: Ein Encoder und Fan-out der codierten Pakete auf bis zu 40
getrennt verschlüsselte WebRTC-Verbindungen. 40 x 8 Mbit/s = 320 Mbit/s Video,
bei 128 kbit/s Stereo-Opus zusätzlich 5,12 Mbit/s, zuzüglich Protokollverkehr
und Wiederholungen. Zwei Rechner sind dafür nicht aufgrund der LAN-Bandbreite
nötig. Fan-out ist eingebaut; der 40-Verbindungen-Test zeigt derzeit eine
Grenze bei Echtzeit-Verarbeitung/Zustellung. Alle Testempfänger laufen auf
dem Senderrechner; ein verteilter Lasttest muss dieses Ergebnis eingrenzen.

Ton ist eingebaut: NDI/OMT-Programmton aus derselben
Quelle, Audio-Samples und Video mit gemeinsamer Quellzeit auf eine Timeline,
Opus und Video in derselben PeerConnection. Gemeinsame RTCP-Zuordnung/CNAME,
korrekte PTS einschließlich Opus-Lookahead und begrenzte Audio-/Videopuffer
erhalten den relativen Zeitpunkt. Ein gemeinsamer, langsam nachgeführter
Quelltakt ordnet Audio und Video der Senderzeit zu. Der Basis-Puffer beträgt
20 ms. Zeitcode-Sprünge über eine Sekunde erfordern einen Quellen-Neustart.
Die ersten zwei Quellkanäle werden übertragen; Mono wird auf Stereo dupliziert.
NDI-Pegel werden mit SMPTE-Headroom von 20 dB auf digitale Vollaussteuerung
abgebildet. Ein echter 30-Minuten-Test mit optisch/akustischem Vergleich ist
noch ausstehend; die beschleunigte Drift-Simulation ersetzt ihn nicht.
Eine gemeinsame PeerConnection allein behebt falsche Zeitstempel nicht.

Grundlagen:
- https://gitlab.com/AOMediaCodec/SVT-AV1/
- https://aomediacodec.github.io/av1-rtp-spec/
- https://developer.mozilla.org/en-US/docs/Web/Media/Guides/Formats/WebRTC_codecs
- https://www.rfc-editor.org/rfc/rfc7941
- https://www.rfc-editor.org/rfc/rfc8834.html

Die UTC-Rohdifferenz enthält Rechner-Uhrenversatz; sie ist nur bei ausreichend
gleichen Rechneruhren als Videolatenz interpretierbar. Korrigierte Schätzung
misst den Abschnitt FastVideo-Eingang bis Browser. Die gesamte Kette ab
NDI-/OMT-Originalbild muss mit dem ursprünglichen Quellzeitstempel oder
einem optischen Vergleich derselben eingeblendeten Uhr gemessen werden.


### Aktueller geprüfter Stand (2026-10-04)

H.264 High/NVENC P7, 720p50, Ziel 8 Mbit/s und Stereo-Opus sind der Standard.
Zwei und zehn echte WebRTC-Testempfänger laufen im Kurztest mit knapp 50 fps;
40 Empfänger bestehen den Echtzeittest nicht. Der Test läuft vollständig
auf dem Senderrechner und ersetzt keinen verteilten Browser-Lasttest.
Die Grenze von 40 ist eine Verbindungsobergrenze, keine Kapazitätsgarantie.
Details und Messgrenzen stehen in TESTBERICHT.md.

Der Viewer versucht nach Verbindungs- oder Bildausfall automatisch erneut
zu verbinden, mit bis zu 10 s Abstand. Quellenneustart benötigt keinen
Seiten-Reload. Trennen beendet die automatischen Versuche. Browser, die
Autoplay mit Ton einschränken, benötigen weiterhin einen Klick auf Ton einschalten.


## Go/Pion-Betrieb (aktueller Standard)

Start-FastVideo.cmd startet gateway/fastvideo-gateway.exe und Python mit
--gateway. Der Browserlink bleibt auf Port 8879. Python leitet nur WHEP-HTTP
und Sitzungsfreigaben an den lokalen Go-Dienst weiter; SRTP läuft direkt
zwischen Go und jedem Browser über UDP 8880.

Python → Go: begrenzte TCP-Verbindung auf 127.0.0.1:9999. FV01-Framing enthält
Medienart, Keyframe-Kennung, originale Medien-PTS und gemeinsame NTP-Zeit.
H.264 wird in vorhandenen RTP-Payloads gebündelt, Opus als codiertes Paket.
Keine Rohbilder oder zusätzlichen Zuschauer-Encoder. Go nutzt getrennte
Sendepuffer (5 Videobilder / 10 Audiopakete); nach Videopufferüberlauf wartet
der betroffene Viewer auf einen Keyframe. PLI/FIR fordert lokal einen IDR an.
RTCP-Senderberichte erhalten das exakte RTP/Quellzeit-Paar und gemeinsame CNAME.

Go/Pion-Version: gateway/go.mod und go.sum (gepinnt). EXE im Paket enthalten;
zum normalen Start ist kein Go SDK erforderlich. Quellcode, Build-Gateway.ps1
und Abhängigkeitslizenzen liegen daneben. Native Testempfänger können auf
einem separaten Rechner mit gateway/fastvideo-load.exe --base
http://SENDER-IP:8879 --viewers 10 --seconds 30 --output test.json laufen.
Diese zählen RTP-Markerpakete und Opus-Pakete, decodieren aber nicht.

40er-Test: 49,8 Video-Markerpakete/s pro Empfänger, 50 Audiopakete/s,
324 Mbit/s Nutzlast, keine Go-Sendepufferverluste (20 Sekunden).
100 Gesamtverbindungen: 30-Sekunden-Test nicht bestanden; Video-Pufferverluste,
worst case 4,43 Markerpakete/s trotz durchgehendem Audio. Standard bleibt 40.
Ein verteilter Test muss Grenzen von Sender und Testempfängern trennen.

Ein Go-Prozessausfall wird im Senderstatus angezeigt. Start-FastVideo.cmd
startet beide Prozesse bei regulärem Neustart; eine durchgehende automatische
Prozessüberwachung sowie Internetbetrieb sind noch nicht implementiert.
Frühere Angaben zum Python-Fanout sind historische Zwischenstände;
aktuelle Ergebnisse stehen am Ende des TESTBERICHTs.


Browser-Mehrfachtest: `http://SENDER-IP:8879/static/loadtest.html`. 5, 10, 20
oder 40 unabhängige Verbindungen mit Videodecoding in einer Seite.
Im Vordergrund lassen; Ton bleibt stumm. Test beenden gibt alle Testplätze frei.

Der Vergleichsschalter 'Nur ein Bild anzeigen' spart viele sichtbare
Videokacheln. Er garantiert nicht, dass der Browser die übrigen Streams
nicht decodiert. Die Anzeige zählt weiterlaufende Decoder mit.
Ein verteilter Test (z.B. 20 auf einem PC und 10 auf einem NUC) ist für
viele echte Zuschauer aussagekräftiger als 40 Decoder auf einem PC.


Aktueller Startstandard: H.264 High / NVENC P7 / 6 Mbit/s / 720p50.
fastvideo.settings.json speichert Bitrate, Encoderstufe und Viewergrenze.
Die lokale Senderseite bietet Encoderwahl und Diagnose je Zuschauer-ID.
Anwenden unterbricht Medienverbindungen kurz. Der normale Viewer verbindet
sich erneut; Mehrfachtests nach Unterbrechung manuell neu starten.
Start-FastVideo startet einen lokalen Supervisor; Abstürze von Python/Go
werden aufgefangen, letzte Quelle wird wieder verbunden. Stop-FastVideo
beendet auch den Supervisor. Der Stoppen-Knopf in der Weboberfläche hält
nur die Übertragung an und entfernt die automatische Quellenwiederaufnahme.
P95 und Budgetüberschreitungen zeigen Encoderreserve über die letzten
bis zu 100 Bilder. Details und konkrete Grenzen im TESTBERICHT.

## Konfigurierbare Ports

`fastvideo.settings.json`: `http_port` (8879) für Webseite/WHEP, `gateway_port` (8880) für WebRTC-UDP und internes Gateway-HTTP, `bridge_port` (9999) für die lokale Medienbrücke. Alle drei müssen verschieden sein. Nach Änderungen FastVideo vollständig stoppen und neu starten. Cloudflare Public Hostname `live.fastvideo.ch`: Service `http://localhost:8879`. Der normale Webtunnel transportiert keinen WebRTC-UDP-Medienstrom; externe Zuschauer benötigen eine direkte ICE-Verbindung oder TURN.


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


### Intel B580 / AV1 (2026-10-06)

Aktuelle Einstellungen: codec=av1, encoder=qsv, bitrate=2000000.
PyAV av1_qsv nutzt Intel Quick Sync, NV12, async_depth=1, look_ahead=0,
low_delay_brc=1, keine B-Frames. Fehlende Hardware bei encoder=qsv fuehrt
zu einem Fehler statt unbemerktem CPU-Encoding. Auto kann auf SVT zurueckfallen.
Go-Gateway nimmt --codec h264 oder av1; beide teilen einen Encoder und Opus.
AV1-Viewer muessen AV1 fuer WebRTC anbieten. Kein paralleler H.264-Fallback.
Die Bitratensteuerung im Sender behaelt den aktiven Codec bei.
Der Viewer verwirft abgebrochene Verbindungsversuche und verbindet nach
Serverausfall bzw. Wiederherstellung einer Browser-Cache-Seite erneut.
Test: node tests/test_viewer_reconnect.js; Hardwaretest mit
FASTVIDEO_TEST_QSV=1 und python -m unittest tests.test_av1_qsv.

Intel-AV1-Qualitaetsstufen: qsv_preset medium / slow / veryslow.
Im Sender ueber Encoderstufe waehlbar und dauerhaft gespeichert. Aktuell
veryslow bei 2 Mbit/s; B-Frames, Lookahead und zusaetzliche Encoderpuffer
bleiben deaktiviert. Profil-/Preset-Qualitaet ersetzt keine ausreichende Bitrate.

Aktueller Betrieb nach 50p-Nachtest: Medium, 2 Mbit/s. Empfang fragt native Quellen mit maximal 5 ms Wartezeit ab, damit faellige Bilder zeitnah freigegeben werden. OpenCV nutzt einen Resize-Worker. Audio-Synchronitaetspruefung vergleicht direkt Audio- und Video-Quellzeitcodes statt Audiozeit mit lokaler Laufzeit.


Pipeline-Diagnose: `/api/status` enthaelt `pipeline_timings` und
`encoder_stage_timings` (Mittel, p95, Maximum aus maximal 250 letzten Werten
pro Stufe). Empfangszeit umfasst auch SDK-Warten; Codeczeit bei AV1 umfasst
Konvertierung und GPU-Upload. Zaehler `input_frame_count`, `frames` und
`video_encode_count` trennen Eingang, Bildfreigabe und Encoding.
`frame_selection_drops`, `frame_deadline_drops`, `video_track_skips` zeigen
unterschiedliche Stellen, an denen Bilder uebersprungen werden.
`source_timecode_gap_frames` schaetzt groessere Quellzeitcode-Abstaende;
daraus keine Netzwerk-Paketverluste ableiten. Fuer einen laufenden Abschnitt
immer Zaehlerdifferenzen betrachten, da Startphasen enthalten sind.
Keine Einzelbild-Logs und kein dauerhaft laufender Lasttest notwendig.


Automatische Codecwahl: Bei AV1-Betrieb mit Go-Gateway wird dasselbe
NDI/OMT-Eingangsbild einmal empfangen und als AV1 sowie als H.264-Fallback
encodiert. AV1 verwendet die Senderbitrate, aktuell 2 Mbit/s; H.264 Intel
QSV Baseline verwendet aktuell 4 Mbit/s. Die NV12-Farbkonvertierung wird
mit einem wiederverwendeten Konverter und einem Worker geteilt. Jeder
Encoder erhaelt eigene Frames. Encoder werden vor Empfangsbeginn vorbereitet.
Opus wird einmal encodiert; beide Videoformate teilen dessen Quellzeitbasis.
Der Viewer waehlt aus dem tatsaechlichen SDP-Angebot AV1 bevorzugt, sonst
H264. Der Go-Gateway routet nur das ausgewaehlte Format zum Client; die
Gesamtgrenze von 40 Zuschauern gilt fuer beide Varianten zusammen.
`/viewer?codec=h264` erlaubt den direkten H264-Qualitaetsvergleich;
`/viewer` waehlt automatisch. Codec im Verbindungsbadge stammt aus den
Empfangsstatistiken. Client-ID (acht Zeichen der Session-ID) erscheint
dezent unten rechts im Browserbild. Erzeugung/Beendigung mit vollem ID
und Codec steht in work/gateway-supervised.log, aktive Clients samt Codec
unter der lokalen /api/connections. Einblendung liegt ueber dem Video im
Browser, ist kein eingebranntes/entfernungssicheres Wasserzeichen. Laender-
zuordnung ist noch nicht implementiert; ID identifiziert die Verbindung.
Hardwaretest: FASTVIDEO_TEST_QSV=1 und unittest test_h264_qsv.py.

Senderoberflaeche: Links bleiben Pegel, Systemauslastung und Serverlaufzeit sichtbar. Dashboard zeigt Stream-Steuerung, Betriebskacheln und den letzten Browserbericht; Connections zeigt die einzelnen Viewer; Viewer links enthaelt die Zuschauerlinks; Setup enthaelt Quellenwahl und Encoder-Einstellungen. Die Tabs sind direkt ueber /sender#dashboard, /sender#connections, /sender#links und /sender#setup erreichbar. SDI2OMT bleibt extern; dessen OMT-Ausgang kann als normale OMT-Quelle verwendet werden.

Kurzhilfe unter /sender#help erklaert Encoding-P95/Framebudget, Go/Pion-Verteilung, automatische Prozessueberwachung und Latenzmessung. Die Budgetanzeige ist bei Ueberschreitungen gelb beziehungsweise rot; ohne Messdaten bleibt sie neutral.

Die Sender-Seitenleiste zeigt eine lokale Eingangsvorschau mit maximal 5 Bildern/s. Sie nutzt vorhandene Empfangsbilder und zaehlt nicht als WebRTC-Zuschauer. Bei versteckter Senderseite pausieren die Vorschauanfragen.

Live input preview in Setup pausiert die Vorschaubilder ohne Source-/Encoder-Neustart. Nach Serverneustart ist die Vorschau wieder aktiviert. Output fps zeigt die tatsaechliche lokale Bildfreigabe ueber die letzten bis zu 100 Bilder, nicht nur die nominale Quellenbildrate.

Bildfreigabe prueft faellige Bilder vor SDK-Warten sowie vor weiterer Audio-/Bildaufbereitung. Beide Videoencoder werden bei neuer Bildsequenz direkt ueber eine Benachrichtigung geweckt; kein periodisches 4-ms-Polling. BGRA-BGR-Eingangskopie verwendet pixelgleiche native OpenCV-Konvertierung. Getrennte OMT-SDK-/Kopierzeiten stehen unter receiver_timings in /api/status.

Quellenwahl: Im Setup Quelle auswählen und Set source bestätigen. Im gestoppten Zustand wird nur die Auswahl gespeichert; Dashboard → Start streaming startet diese bestätigte Quelle. Bei laufendem Stream übernimmt Set source den Wechsel sofort und verbindet die Zuschauer neu. Die Auswahl bleibt nach Stoppen und Neustart erhalten. Die Übergabe verwendet Quellenart und Adresse statt einer veränderlichen Listenposition.

Users: Open the local sender Users tab to add/edit name and email, allow one or two concurrent connections and copy personal viewer links. JSON data and access keys live in user/users.json; back up that folder separately from the distributable application archive. Name/email are only visible through the local control API. Replace link revokes the old key and closes its sessions; Disabled closes current sessions. Personal links only requires personal links for new admissions; it retains existing general sessions. Limits can change live; reducing a limit retains existing sessions and blocks additional admissions. Same-tab reconnect/reload replaces its previous session. Viewer user codes map to the Users table. General links remain enabled by default for existing workflows.

User links: Export links downloads a compact list with one name and personal viewer link per line. Export mailtexts downloads FastVideo-user-emails.txt with one prepared email per user (Dear user NAME, mail text, personal viewer link), plus recipient/status metadata. The send-specific mail text is read from user/usermail.txt on each export. No email is sent automatically. The user directory remains private and must be backed up separately.
