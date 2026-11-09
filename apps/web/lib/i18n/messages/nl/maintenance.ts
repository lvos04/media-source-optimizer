// 1.6.0 WP-E2 — onderhoud: bewaartermijnen, lichtere back-ups, back-up vóór upgrade, koppelingsbewaking.
export const messages = {
  // Systeem → Onderhoud
  "maintenance.title": "Onderhoud",
  "maintenance.sub": "MSO ruimt elke dag zijn eigen oude taken, metingen en logregels op. Het auditlogboek blijft altijd bewaard; in SABnzbd, qBittorrent, Prowlarr en MediaGuard verandert niets.",
  "maintenance.run": "Nu opruimen",
  "maintenance.runStarted": "Opruimen gestart",
  "maintenance.runStartedBody": "Het onderhoud loopt als achtergrondtaak; je ziet het resultaat hier zodra het klaar is.",
  "maintenance.adminOnly": "Alleen een beheerder kan het onderhoud handmatig starten.",
  "maintenance.disabled": "Dagelijks opruimen staat uit (Instellingen → Systeem). De database groeit dan onbeperkt.",
  "maintenance.lastRun": "Laatste ronde",
  "maintenance.never": "Nog niet gedraaid; de eerste ronde volgt vanzelf binnen een dag.",
  "maintenance.lastRunValue": "{when} · {duration}",
  "maintenance.removed": "Opgeruimd",
  "maintenance.removedValue": "{jobs} taken · {raw} metingen (samengevat per uur) · {logs} logregels",
  "maintenance.vacuum": "Database opgeschoond (VACUUM ANALYZE)",
  "maintenance.vacuumValue": "{count} tabel(len)",
  "maintenance.incomplete": "Deze ronde stopte na 20 minuten; de volgende ronde gaat verder.",
  "maintenance.periods": "Bewaartermijnen",
  "maintenance.periodsValue": "taken {done} d (mislukt {failed} d) · metingen {raw} d, daarna per uur {hourly} d · automatiseringslog {auto} d · inlogpogingen {login} d",
  "maintenance.rawExtended": "Losse metingen worden {days} dagen bewaard in plaats van {configured}, omdat {reader} zo ver terugkijkt.",
  "maintenance.settingsLink": "Bewaartermijnen aanpassen",
  "maintenance.database": "Database",
  "maintenance.dbSize": "Grootte",
  "maintenance.topTables": "Grootste tabellen",
  "maintenance.table": "Tabel",
  "maintenance.tableRows": "Rijen (ca.)",
  "maintenance.tableSize": "Grootte",

  // Systeem → Back-ups (aanvullingen)
  "maintenance.backupSpace": "Vrij {free} · nodig voor een veilige back-up {needed}",
  "maintenance.backupSpaceLow": "Te weinig vrije schijfruimte: de volgende back-up wordt overgeslagen ({free} vrij, {needed} nodig). Maak ruimte vrij op {path}.",
  "maintenance.backupExcluded": "Zoekcache, MediaGuard-cache, header-index en torrent-metingen van de laatste uren gaan niet mee in de back-up; MSO haalt die na terugzetten zelf opnieuw op.",
  "maintenance.backupSafety": "Veiligheidsback-ups vóór terugzetten verdwijnen na {days} dagen (de nieuwste {keep} blijven altijd); van back-ups vóór een upgrade blijven de nieuwste {upgrade}.",
  "maintenance.backupKind.preUpgrade": "Vóór upgrade",

  // Overzicht → Koppelingen
  "maintenance.conn.authSince": "Sleutel geweigerd sinds {age}",
  "maintenance.conn.offlineSince": "Onbereikbaar sinds {age}",
  "maintenance.conn.timeoutSince": "Reageert niet (time-out) sinds {age}",
  "maintenance.conn.lastOk": "laatst gelukt {when}",
  "maintenance.conn.jobsFailed": "{count} taak/taken mislukten hierdoor (24 u)",
  "maintenance.conn.fix": "Controleren",
  "maintenance.status.authFailed": "Sleutel geweigerd",
  "maintenance.status.timeout": "Time-out",

  // aanbevelingen / Wacht op jou
  "automation.area.system": "Systeem",
};
