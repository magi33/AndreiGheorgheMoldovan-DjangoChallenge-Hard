# Django War Simulator — Hard

Aplicatie realizata pornind de la proiectul oficial https://github.com/imimali/coderun2026-starter.

Numele repository-ului de predare trebuie sa fie `<TeamName>-DjangoChallenge-Hard`, cu numele oficial al echipei.

## Pornire rapida pe Windows

Este necesar Python 3.12 (instalat deja in pregatirea pentru concurs).

1. Dezarhiveaza proiectul si deschide folderul in VS Code.
2. Ruleaza `setup.bat` o singura data. Instaleaza dependentele si aplica migrarile.
3. Porneste `run-worker.bat` si lasa fereastra deschisa.
4. Porneste `run-web.bat` si lasa si aceasta fereastra deschisa.
5. Deschide http://127.0.0.1:8000/.

Echivalent in PowerShell, din folderul cu manage.py:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
```

Terminal 1 — worker:

```powershell
.\.venv\Scripts\python.exe -m celery -A coderun2026_starter worker --pool=threads --concurrency=16 --loglevel=info
```

Terminal 2 — server:

```powershell
.\.venv\Scripts\python.exe manage.py runserver
```

Nu trebuie activat manual mediul virtual. Nu sunt necesare Redis, Docker sau un server de baze de date separat.

## Cum folosesti aplicatia

Pe pagina Operations:

1. **Import people** — descarca `/people`, inregistreaza persoanele si cauta cheia lui Hellstorm Fireburn. Asteapta pana cand numarul persoanelor este 100.
2. **Start drafting** — descarca toate loturile urmand `next_batch`, in taskuri separate. Salvarea unui lot si descarcarea urmatorului sunt programate independent. Asteapta pana cand toate persoanele au primit grad.
3. **Import fights** — urmareste toate loturile `/fight/x` si creeaza cate un task separat pentru salvarea fiecarei lupte si a tuturor evenimentelor ei. Asteapta pana cand nu mai sunt taskuri de import active.
4. Deschide **Combat simulator** si apasa **Simulate all fights**. Un task coordonator lanseaza cate un task Celery pentru fiecare lupta.
5. Deschide o lupta pentru istoricul complet, inclusiv sanatatea dupa fiecare atac.

Serverul concursului introduce intarzieri. Operatiile continua in worker; pagina afiseaza progresul. In cazul unui timeout, taskurile reincearca automat.

## Endpointuri locale

- `/` — operatii, contoare si instructiunile recuperate.
- `/people` — persoane/soldati, cautare, filtrare dupa grad si paginare.
- `/people/<id>` — detaliile unei persoane si luptele sale. Acesta este ID-ul local Django; ID-ul extern este afisat separat.
- `/simulate` — simularea tuturor luptelor si statisticile echipelor.
- `/fight/<id>` — istoricul ordonat al unei lupte.
- `/api/status` — starea operatiilor in format JSON.

## Cerinte Hard si implementare

- Roster persistent: modelul `Person`, cu `external_id` unic, nume si varsta.
- Descoperire instructiuni: cheia lui Hellstorm este salvata, iar raspunsurile de la endpointurile de instructiuni sunt afisate pe dashboard.
- Migrare pentru recrutare: `0002` adauga explicit `rank`, `health` si `drafted_at` peste schema initiala.
- Recrutare: task `download_draft_batch` pentru fiecare lot si task separat `save_draft_batch` pentru salvarea lui.
- Retry: maximum 5 reincercari, cu backoff exponential, jitter si timeout HTTP configurabil.
- Import lupte: `download_fight_batch` urmareste `next_batch` sau `next`; `save_fight` salveaza fiecare lupta si toate atacurile ei.
- Simulare: task separat pentru fiecare lupta, pool de 16 fire, cu tranzactii scurte si actualizari `F()` ale sanatatii comune a soldatilor.
- Idempotenta: o lupta finalizata nu aplica din nou aceleasi daune la apasari repetate.
- Evenimente: `FightEvent` pastreaza ordinea, atacatorul, aparatorul, daunele din sursa, pierderea efectiva de sanatate si valorile HP dupa atac.
- Rezultate HTML: lista luptelor, detalii, istoric complet si statistici pe echipe.

Sanatatea este comuna soldatului intre lupte si ramane intre 0 si 100. Daunele originale sunt pastrate separat de pierderea efectiva de HP. Sunt procesate exact evenimentele furnizate de server, inclusiv daca soldatul a ramas deja fara sanatate intr-o alta lupta. Castigatorul este afisat numai daca, la finalul evenimentelor, un participant are HP pozitiv si celalalt are 0; altfel se afiseaza finalizarea reluarii, fara inventarea unor atacuri suplimentare.

SQLite permite un singur writer la un moment dat. Taskurile Celery sunt independente si ruleaza in paralel; sectiunile scurte care scriu in baza de date sunt serializate de tranzactii `IMMEDIATE`. Astfel nu se pierd actualizari de sanatate. Cererile HTTP sunt facute in afara tranzactiilor.

## Configurare

Variabile de mediu optionale:

- `CHALLENGE_HOST`: implicit hostul oficial din enunt.
- `CHALLENGE_TIMEOUT`: implicit 45 secunde.
- `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND`: implicit fisiere SQLite locale, conform quickstart-ului.

Proiectul este pentru rulare locala si evaluare la concurs. Baza de date si mediul virtual sunt excluse din Git.

## Verificari

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py test army --noinput
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
```

15 teste verifica importul, identificatorul extern 0, recrutarea, retry la timeout, idempotenta, pastrarea evenimentelor, rollback, sanatatea comuna intre lupte concurente si paginile HTML.

A fost verificat si fluxul complet cu un worker Celery real, pool threads de 16 fire si raspunsurile HTTP capturate de la serverul oficial: 100 persoane, 10 loturi de recrutare, 10 loturi de lupte, 300 lupte si 1.544 evenimente. Toate valorile finale de sanatate au fost comparate cu un calcul independent. Mediul de test a fost Linux / Python 3.12; fisierele BAT sunt pregatite pentru rulare pe Windows.

## Predare pe GitHub

1. Creeaza repository-ul cu numele oficial `<TeamName>-DjangoChallenge-Hard`.
2. Publica sursele din acest folder, inclusiv `requirements.txt`, migrarile, template-urile si fisierele statice.
3. Pastreaza `.gitignore`; nu publica `.venv`, bazele de date sau logurile.
4. Include linkul repository-ului in formularul organizatorilor.

## Documentatie

- https://docs.djangoproject.com/en/5.2/topics/db/models/
- https://docs.djangoproject.com/en/5.2/topics/templates/
- https://docs.djangoproject.com/en/5.2/ref/databases/#transactions-behavior
- https://docs.celeryq.dev/en/stable/userguide/tasks.html
