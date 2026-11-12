# FastVideo WHIP Bridge

**Website und Beschreibung der Bridge: [fastvideo.ch](https://fastvideo.ch/)**

FastVideo Bridge ist ein eigenständiger Sender und Empfänger für Live-Video unter Windows. Die Bridge verbindet OMT, NDI und Blackmagic SDI mit WHIP, WHEP und SRT und wird über eine lokale Weboberfläche bedient.

**[Windows-Installer herunterladen (Version 0.0.5.14)](https://github.com/peteraellig/FastVideoBridge/releases/download/v0.0.5.14/FastVideoBridge-Setup-0.0.5.14.exe)** · [Alle Releases](https://github.com/peteraellig/FastVideoBridge/releases)

![FastVideo Bridge – Sender und Empfänger mit Live-Vorschau](images/fastvideobridge-screenshot.png)

## Funktionen

- OMT-, NDI- und Blackmagic-SDI-Quellen als Videoeingang.
- WHIP-Sender mit H.264, H.265 oder AV1 und Opus-Audio.
- SRT-Sender im Caller-Modus mit H.264/H.265 und AAC-Audio.
- WHIP-Empfang sowie Empfang von FastVideo-Links über WHEP.
- Ausgabe empfangener Signale über OMT, NDI oder Blackmagic SDI.
- Hardwarebeschleunigung abhängig von Codec, Grafikhardware und Treiber.
- Live-Vorschau, Audioauswahl und Diagnoseanzeigen in der Weboberfläche.

## Installation

1. Den [Windows-Installer](https://github.com/peteraellig/FastVideoBridge/releases/download/v0.0.5.14/FastVideoBridge-Setup-0.0.5.14.exe) herunterladen und ausführen.
2. FastVideo Bridge über die angelegte Verknüpfung starten.
3. Die Bedienoberfläche öffnet sich unter [http://127.0.0.1:18879/sender](http://127.0.0.1:18879/sender).
4. Quelle, Sendeziel oder Empfang konfigurieren und starten. Zum Beenden die Verknüpfung **Stop FastVideo Bridge** verwenden.

Voraussetzung ist Windows x64. Python, der Go-Gateway und die OMT-Laufzeit sind im Installer enthalten. Für NDI-Ein-/Ausgabe müssen [NDI Tools](https://ndi.video/tools/) separat installiert werden. Für Blackmagic SDI wird Blackmagic Desktop Video benötigt. Aktuelle Grafiktreiber werden empfohlen.

Für eingehenden WHIP-Verkehr müssen der gewählte Empfangsport und gegebenenfalls die Router-Portweiterleitung eingerichtet sein. Hinweise dazu stehen in der Hilfe der Bridge. WHEP-Empfang baut eine ausgehende Verbindung auf.

Die [SHA-256-Prüfsumme](https://github.com/peteraellig/FastVideoBridge/releases/download/v0.0.5.14/FastVideoBridge-Setup-0.0.5.14.exe.sha256) liegt beim Release. Der fertige Installer wird als Release-Datei bereitgestellt; er ist nicht Bestandteil des Git-Quellcodearchivs.

## Selbst bauen

Benötigt werden Python 3.12 x64 und Go 1.26 oder neuer. In PowerShell im Projektverzeichnis:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r documents\requirements.lock.txt
Copy-Item fastvideo.settings.example.json fastvideo.settings.json
Push-Location gateway
go build -mod=vendor -trimpath -o fastvideo-gateway.exe .
Pop-Location
.\Start-Bridge.bat
```

Eine bereits vorhandene persönliche Konfiguration nicht überschreiben. Weitere Schritte zum Erstellen eines Windows-Installers stehen in [BUILD.txt](BUILD.txt).

`gateway/vendor` enthält die benötigten Go-Bibliotheken einschließlich einer lokalen GoSRT-Anpassung. Diesen Ordner beim Build beibehalten. `vendor/omt` enthält die benötigten nativen OMT-Bibliotheken und deren Lizenz.

## Dokumentation

- [Beschreibung und Bedienung](documents/BRIDGE-README.md)
- [Build-Anleitung](BUILD.txt)
- [Weitere Projektdokumente](documents/)

Historische Entwicklungsdokumente können lokale Pfade und ältere Versionsstände enthalten. Testsuiten, Laufzeitlogs, persönliche Einstellungen und Arbeitsdaten gehören nicht zu diesem Quellcode-Export.

## Lizenz

Der eigene FastVideo-Bridge-Code steht unter der **BSD Zero Clause License (0BSD)**, siehe [LICENSE](LICENSE).

Mitgelieferte Drittanbieterkomponenten behalten ihre jeweiligen Lizenzen. Die Lizenztexte befinden sich insbesondere in `gateway/vendor`, `gateway/third_party_licenses` und `vendor/omt/LICENSE.txt`. Im Windows-Installer sind weitere Lizenztexte bei den enthaltenen Laufzeitkomponenten hinterlegt.

NDI® ist eine eingetragene Marke von Vizrt NDI AB.