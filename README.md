# Sat Track für Android

Bedienung (vier Bildschirme unten):
- Einstellungen: Standort eintragen und Speichern.
- Satelliten: NORAD-ID (ISS: 25544) oder Name suchen. Danach die Frequenz aus der SatNOGS-Liste wählen
  (oder selbst eingeben), Namen und CTCSS prüfen, Speichern. Derselbe Satellit kann mit mehreren
  Frequenzen unter verschiedenen Namen gespeichert werden.
- Überflüge: Berechnen. Die ersten 16 sind vorgewählt. Einzelne Überflüge antippen zum Wählen oder
  Abwählen (höchstens 16), dann "Gewählte auf Funkgerät laden".
- Funkgerät: Anzeigen, Uhr kalibrieren, SatTrack-App installieren, alles löschen.
  Per USB-C (OTG) anschließen, beim ersten Mal USB-Zugriff erlauben.

Hinweise
- Bahndaten kommen von CelesTrak und werden 24 Stunden zwischengespeichert. Meldet CelesTrak einen
  Serverfehler (403, 429, 5xx), fragt die App 30 Minuten lang nicht erneut an.
- Bauen: Repo auf GitHub, Actions > Build APK > Run workflow, Artefakt "sattrack-apk" laden.
