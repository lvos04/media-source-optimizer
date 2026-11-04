# All-in-one image — testrapport

Image: `media-source-optimizer:local` · 9 scenario's · 9 geslaagd

| Scenario | Resultaat | Details |
|---|---|---|
| 1 root + lege map → gezond, eigenaar 568, processen als 568 | ok | health=healthy; 568:568 /d/postgres, 568:568 /d/secrets, 568:568 /d/backups; proces-uids (excl. PID 1)=[568] |
| 2 setup, login, CSRF, koppeling + verbindingstest | ok | admin 200; zonder CSRF 403; SABnzbd-test healthy v4.3.3 |
| 3 back-up via worker + herstel | ok | job done; 2 back-up(s); herstel 200; sleutel na herstel: ingesteld |
| 4 docker restart → data en login blijven | ok | health=healthy |
| 5 harde kill (SIGKILL) → opnieuw starten werkt | ok | postmaster.pid achtergebleven=True; health=healthy; melding=True |
| 6 netjes stoppen | ok | 0.5 s, exitcode 0 |
| 9 geen geheimen in de log | ok | 7572 bytes log gecontroleerd |
| 7 --user 568 op map van 568 → gezond | ok | health=healthy |
| 8 --user 568 op root-map → duidelijke foutmelding | ok | [mso] FOUT: geen schrijfrechten op /appdata/postgres. Geef gebruiker 568 (apps) schrijfrechten op de dataset, of laat de app als root starten zodat MSO dit zelf regelt. |
