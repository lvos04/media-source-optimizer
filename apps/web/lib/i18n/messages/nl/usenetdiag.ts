// 1.6.0 WP-A: Usenet — the real cause (SABnzbd's processing log) and retrying only when it can help.
// Downloads → Usenet (facts per failed download, reclassify) and Usenet-providers → Autopilot (retry part).
export const messages = {
  // facts per failed download (Downloads → Usenet)
  "usenetdiag.facts.complete": "Data compleet",
  "usenetdiag.facts.completeRepaired": "Data compleet ({n} ontbrekende artikelen hersteld)",
  "usenetdiag.facts.missing": "{n} artikelen ontbraken",
  "usenetdiag.facts.malformed": "{n} artikelen beschadigd",
  "usenetdiag.facts.aborted": "SABnzbd vond de artikelen niet (download afgebroken)",
  "usenetdiag.facts.repairFailed": "herstel mislukt",
  "usenetdiag.facts.repairFailedShort": "herstel mislukt ({n} blokken tekort)",
  "usenetdiag.facts.unpackFailed": "uitpakken mislukt: {line}",
  "usenetdiag.facts.environment": "Ligt aan SABnzbd of de opslag ({problem}), niet aan de servers of de release.",
  "usenetdiag.facts.release": "Ligt aan deze release ({problem}); een andere release kan wel lukken.",
  "usenetdiag.facts.age": "Leeftijd van de post: {days} dagen",
  "usenetdiag.facts.servers": "Gedownload van: {servers}",
  "usenetdiag.facts.title": "Uit het verwerkingslog van SABnzbd",

  // reclassify (Downloads → Usenet, history card)
  "usenetdiag.reclassify": "Opnieuw beoordelen",
  "usenetdiag.reclassifyTitle": "Alle mislukte downloads opnieuw beoordelen met het verwerkingslog van SABnzbd (ook het archief). Verandert alleen de oorzaak die MSO toont; in SABnzbd verandert niets.",
  "usenetdiag.reclassifyDone": "{checked} mislukte downloads opnieuw beoordeeld: {changed} kregen een andere oorzaak, {complete} hadden complete data. {gone} staan niet meer in SABnzbd en houden hun oude oorzaak.",
  "usenetdiag.reclassifyLast": "Laatst opnieuw beoordeeld {when}: {changed} van {checked} kregen een andere oorzaak.",

  // retry (Usenet-providers → Autopilot)
  "usenetdiag.retry.efficacy": "Laatste {days} dagen: opnieuw geprobeerd {n} · gelukt {pct}",
  "usenetdiag.retry.efficacyNone": "Laatste {days} dagen: nog geen afgeronde pogingen.",
  "usenetdiag.retry.efficacyRunning": "{n} loopt nog",
  "usenetdiag.retry.rule": "MSO probeert een mislukte download alleen opnieuw als er artikelen ontbraken (of SABnzbd zelf een serverprobleem noemde) én de dekking daarna verbeterde: een server werkt weer of er kwam een nieuwe bij. Downloads met complete data waarvan het uitpakken mislukte, probeert MSO niet opnieuw.",
  "usenetdiag.retry.lastGain": "Laatste verbetering van de dekking: {what} ({when}).",
  "usenetdiag.retry.noGain": "Sinds MSO meekijkt is de dekking nog niet verbeterd; opnieuw proberen wacht daarop.",
  "usenetdiag.retry.paused": "Op pauze",
  "usenetdiag.retry.resume": "Hervatten",
  "usenetdiag.retry.resumeTitle": "Automatisch opnieuw proberen weer aanzetten. Pogingen van vóór nu tellen dan niet meer mee voor de pauze-regel.",
  "usenetdiag.retry.resumed": "Automatisch opnieuw proberen staat weer aan",
  "usenetdiag.retry.brakeRule": "Pauzeert vanzelf bij minstens {min} afgeronde pogingen in {days} dagen waarvan minder dan {pct} lukt.",
  "usenetdiag.retry.checked.paused": "Opnieuw proberen staat op pauze",
} as const;
