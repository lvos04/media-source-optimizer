# ADR-0006 — Omzetting van de MSO-web-UI naar Peil: interpretaties

Status: **Aanvaard (ontwerpstap, nog niets gebouwd)** · Datum: 2026-11-09 · Excel: geen eigen ID (contract 1.2 §13,
conformiteit MS6 "Uiterlijk volgens de ontwerptaal")
Hoort bij: `docs/ontwerp/PEIL_MIGRATIE.md` · Ontwerptaal: Peil 1.1.0 (`design/ontwerptaal/`)

## Context

Contract 1.2 §13 zet MSO in de reikwijdte van de platform-ontwerptaal: **alleen het uiterlijk**; functies, routes,
gebruikersreizen en API's blijven gelijk. De gebruiker stond twee toevoegingen toe: een link "Terug naar de Hub" (adres
uit de configuratie, verborgen als onbekend) en een tabelweergave met toetsenbordbediening bij de grafieken. Voor deze
ronde geldt verder: OpenAPI byte-gelijk, `data-testid`'s en rollen blijven, geen versie-ophoging. De ontwerptaal
(ONTWERPTAAL §13.3/§13.4) beschrijft het pad voor MediaGuard en MSO, maar laat enkele MSO-specifieke punten open of
botst met deze vaste regels. Dit ADR legt de veiligste interpretatie vast.

De repo-kopie van de contracten is versie 1.1; §13 bestaat pas in 1.2 (`ai-server-manager`, bron van waarheid). Bij
verschil wint de bron; dit ontwerp volgt 1.2 §13.

## Besluiten

1. **Hub-adres = optionele instelling `systeem.hub_url`.** MSO kent geen Hub-adres (geen enrollment in v1, conformiteit
   MS1; geen `HUB_URL`). Gekozen: één nieuwe definitie in het bestaande instellingenregister (type string, standaard
   leeg), gelezen en geschreven via het bestaande `GET/PUT /api/v1/settings`. Geen nieuwe route, geen migratie (de
   waarden staan in de bestaande `settings`-tabel), OpenAPI blijft byte-gelijk (de respons van `GET /settings` heeft
   geen schema), het Custom App-formulier verandert niet. Validatie volgens contract 1.2 §3.2 (`HUB_URL`-regels) in de UI
   (link alleen bij een geldig `http(s)`-adres; anders niet in de DOM) en bij opslaan (422).
   *Verworpen:* een omgevingsvariabele (verandert de installatie/formuliervelden), een veld in `GET /system/info`
   (verandert het OpenAPI-schema), een vast of voorbeeldadres (NAS-gegevens zijn onbekend).
2. **DangerDialog blijft een dialoog.** Peil zegt "R2/R3-goedkeuring nooit in een (kleine) Dialog: een eigen pagina".
   Dat is de goedkeuringsflow van de **Hub** (GoedkeuringsPagina). In MSO zou een eigen pagina een nieuwe route en een
   andere gebruikersreis zijn. De bestaande bevestigingen (terugzetten, terugdraaien, pauzeren) worden een shadcn
   AlertDialog in Peil-stijl (getypte bevestiging, gevaarknop `aria-disabled` tot de naam klopt, mobiel volledige
   hoogte) met dezelfde rollen, id's en knopnamen.
3. **Backendteksten blijven; de UI vertaalt.** De core geeft in `message` Nederlandse statuswoorden ("Gezond",
   "Verstoord", "Offline"). Die blijven (API-uitvoer). De UI toont het Peil-woord uit de statuscode (Beperkt, Storing,
   Onbekend) en gebruikt `message` alleen als reden als het geen kaal statuswoord is.
4. **MSO-risiconiveaus worden R-klassen in beeld, niet in data.** `advies` → R0 · Alleen lezen, `automatisch_veilig` →
   R1 · Omkeerbaar, `toestemming_nodig` → R2 · Goedkeuring nodig (ONTWERPTAAL §13.4). Het API-veld `risk` en de
   betekenis blijven; de MSO-naam blijft zichtbaar als uitleg ("MSO-niveau: automatisch veilig — …").
5. **Twee fasen.** De gebruiker vraagt fase 1 (aliaslaag) en fase 2 (Tailwind 4 + shadcn/ui). ONTWERPTAAL §13.3 noemt
   voor MediaGuard ook een fase 3 (inline styles en `mg.css` weg). Voor MSO is dat het slot van fase 2 ("2d
   opruimen"). Woorden en iconen (zonder nieuwe structuur) horen bij fase 1 ("1b").
6. **MSO-eigen correctielaag naast de gedeelde aliaslaag.** `aliaslagen/mediaguard-mso.css` is gegenereerd en mag niet
   met de hand worden gewijzigd. Correcties die alleen MSO nodig heeft, of die in de gedeelde laag niet werken, komen in
   een repo-eigen `apps/web/styles/peil-mso.css` ná de aliaslaag. Gemeten oorzaak voor de belangrijkste: Peils
   focusring en `scroll-padding` staan in `@layer base` en verliezen van de ongelaagde `mg.css`; dat geldt ook voor
   MediaGuard en is als vervolgpunt voor de ontwerptaal gemeld.
7. **Overloop meten t.o.v. de apparaatbreedte.** Met `isMobile` rekt Chromium het layout-viewport op tot de breedste
   inhoud; `scrollWidth − innerWidth` is dan altijd 0. De bestaande e2e-controle zag daardoor 6 overlopende schermen
   niet. Nieuwe en aangepaste tests vergelijken met de ingestelde viewportbreedte.
8. **Woorden voor afgeronde taken en downloads.** Peil gebruikt "Gelukt"/"Klaar" niet meer en reserveert groen voor
   gemeten gezondheid. Taken: **Afgerond** (neutraal `check`); downloads: **Voltooid** (het woord van SABnzbd/qBittorrent);
   wijzigingen: **Toegepast**, en "Uitgevoerd en gecontroleerd" pas als de MSO-controle de wijziging heeft behouden.
9. **Geen Android en geen en-US voor MSO.** MSO heeft geen Android-app en is alleen Nederlands; de besluiten over
   Android en en-US gelden voor MediaGuard en my_house.
10. **Geen platform-statuspagina voor MSO.** Het statuspagina-sjabloon (ONTWERPTAAL §13.2) is voor de vijf nieuwe
    diensten; MSO houdt zijn eigen UI.

## Gevolgen

- Fase 1 is terug te draaien door twee `@import`-regels weg te halen; elke fase-2-stap is één commit met groene e2e.
- Eén functionele toevoeging in de core (instelling + validator, §9.1 van het ontwerp); geen schema- of routewijziging.
- De e2e-suite krijgt een strengere overloopmeting en de axe-tag `wcag21a`; die vinden nu bestaande fouten
  (profielknop 2.5.3, `aria-label` op `span.qribbon`), die in fase 1b worden opgelost.
- Niet gevalideerd: echte telefoon, schermlezer, forced colors en 200 %-zoom op MSO zelf.
