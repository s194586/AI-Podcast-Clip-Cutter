# Viral Cutter AI - Workflow Manager

Aktualny MVP to podcast/talking-head cutter. Manager ma sluzyc do lokalnego przygotowania klipow z rozmow, wywiadow, podcastow i dlugich materialow mowionych.

Nie rozwijamy aktywnie trybow `gameplay`, `tutorial`, `commentary` ani `generic`. Jezeli stare moduly jeszcze istnieja w repozytorium, sa traktowane jako legacy/dead code i nie powinny byc wybierane przez obecny workflow.

## Glowny Workflow

```powershell
.\.venv\Scripts\python.exe manager.py --content-type auto --ai-mode local_only --subtitle-checker-mode local_only
```

`--content-type auto` jest dozwolone, ale w MVP mapuje sie na `podcast`.

Kroki workflow:

1. przygotowanie folderow roboczych
2. pobranie lub wskazanie materialu zrodlowego
3. transkrypcja lokalna, jezeli nie ma cache
4. diarization jako analiza wewnetrzna
5. podcast-only routing
6. lokalne scoring i wybor momentow
7. ciecie klipow
8. kadr 9:16 pod talking-head
9. jeden stabilny styl napisow

## Komponenty

- `download_content.py` - pobieranie zrodel, metadanych i audio
- `transcribe.py` / backendi w `transcription/` - lokalna transkrypcja
- `subtitler_checker.py` - lokalna lub opcjonalna AI kontrola transkrypcji
- `analyze_virals.py` - wybor momentow jako podcast story beats
- `cutter.py` - render 9:16
- `subtitler.py` - napisy bez kolorowania po speakerach
- `benchmark.py` - lokalny podcast-only benchmark

## Napisy

W MVP napisy maja jeden stabilny styl. Rozpoznawanie speakerow moze zostac w metadanych i diagnostyce, ale nie steruje kolorem napisow.

## Benchmark

Domyslny batch podcast-only:

```powershell
.\.venv\Scripts\python.exe tools\run_local_benchmark.py --review-batch podcast_only_v1
```

Dashboard:

```powershell
start benchmarks\review_dashboard.html
```

Manual review ma ocenic:

- logiczny start
- kontekst przed odpowiedzia
- rozwiniecie i pointa
- brak uciecia zdania
- synchronizacje napisow
- czytelny podzial napisow
- stabilny kadr/speaker continuity
- samodzielna historia w klipie

## Bezpieczenstwo

Nie usuwaj ani nie commituj:

- `.env`
- `.venv/`
- `input/`
- `benchmarks/assets/`
- recznych ocen human review bez backupu

Artefakty typu `benchmarks/runs/`, `benchmarks/results.json`, `benchmarks/report.md` i `benchmarks/review_dashboard.html` sa odtwarzalne.
