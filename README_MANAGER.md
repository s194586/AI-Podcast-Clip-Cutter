# 🎬 Viral Cutter AI - Workflow Manager

Kompletny system automatyzacji: od pobrania wideo z YouTube, przez transkrypcję, analizę viralowych momentów, wycinanie, do dodawania dynamicznych napisów.

---

## 📋 Komponenty

### 1. `manager.py` — Główny Orkestrator
**Główny skrypt** - automatyzuje cały workflow w jednym poleceniu.

```bash
# Pełny workflow z pobieraniem
python manager.py --url "https://www.youtube.com/watch?v=..."

# Cleanup: usuń ciężkie pliki z input/ po sukcesie
python manager.py --url "..." --cleanup

# Test/Debug: pomiń pobieranie i transkrypcję
python manager.py --url "..." --skip-download
```

**Workflow Manager:**
1. ✅ Sprawdza i tworzy niezbędne foldery (input/, metadata/, transcripts/, cuts/raw/, cuts/subtitles/)
2. 📥 **Pobieranie** - `download_content.py` (wideo z YouTube max 1080p, audio, metadane)
3. 🎙️ **Transkrypcja** - `transcribe_podcast.py` (dzielenie po ciszy, Google Gemini API)
4. 📊 **Analiza** - `analyze_virals.py` (heatmapa + transkrypcja → top momenty)
5. ✂️ **Wycinanie** - `cutter.py` (30-60s segmenty na bazie heatmapy)
6. 📝 **Napisy** - `subtitler.py` (białe napisy z czarnym obrysem, dolna 1/3 ekranu)
7. 🧹 **Cleanup** (opcjonalnie) - usuwa wideo z input/ po sukcesie

**Obsługa błędów:**
- Retry logic dla transkrypcji (2 próby)
- Jasne komunikaty o błędach
- Graceful shutdown na Ctrl+C

---

### 2. `subtitler.py` — Dynamiczne Napisy
Dodaje napisy na wycięte wideo z transkrypcji.

```bash
python subtitler.py \
  --transcript transcripts/Naruciak_Final.json \
  --input-dir cuts \
  --output-raw cuts/raw \
  --output-subs cuts/subtitles
```

**Funkcjonalność:**
- ✅ 1-3 słowa na ekranie (dynamiczny rozsplit transkrypcji)
- ✅ Duża, czytelna czcionka (Arial 60pt)
- ✅ Białe napisy z czarnym obrysem (2px)
- ✅ Umieszczone w dolnej 1/3 ekranu (MarginV=80)
- ✅ Bezpieczne strefy (nie zasłaniają UI TikToka/Shortsów)

**Output:**
- `cuts/raw/` - surowe wideo (bez napisów)
- `cuts/subtitles/` - wideo z wbijanymi napisami (burnt-in)

---

### 3. Pozostałe Komponenty

#### `download_content.py`
```bash
python download_content.py "https://www.youtube.com/watch?v=..."
```
- Pobiera wideo (max 1080p) i audio
- Tworzy metadane (.info.json) i heatmapę (placeholde jeśli nie ma)

#### `transcribe_podcast.py`
```bash
python transcribe_podcast.py --file input/audio.mp3 --out transcripts/output.json
```
- Transkrybuje audio dzieląc po ciszy
- Używa Google Gemini API
- Retry logic

#### `analyze_virals.py`
```bash
python analyze_virals.py \
  --transcript transcripts/Naruciak_Final.json \
  --heatmap metadata/heatmap.json \
  --save-json top_windows.json
```
- Analizuje transkrypcję i heatmapę
- Znajduje 3 najlepsze momenty (30-60s)

#### `cutter.py`
```bash
python cutter.py --windows top_windows.json --output-dir cuts
```
- Wycina segmenty z wideo
- Format: `segment_1_MM-SS_ms_MM-SS_ms.mp4`

---

## 📁 Struktura Katalogów

```
project/
├── input/                          # Pobrane wideo/audio
├── metadata/                       # Metadane (.info.json) i heatmapy
├── transcripts/                    # Transkrypcje (JSON)
│   ├── Naruciak_Final.json        # Główna transkrypcja
│   └── cache/                      # Temp cache dla transkrypcji
├── cuts/                           # Wycięte segmenty
│   ├── segment_*.mp4              # Surowe wycinki (tymczasowe)
│   ├── raw/                        # Surowe wycinki (bez napisów)
│   └── subtitles/                 # Wycinki z napisami (FINAL)
├── top_windows.json                # Wybrane momenty
├── manager.py                      # 🎬 GŁÓWNY ORKESTRATOR
├── subtitler.py                    # 📝 Dodawanie napisów
├── download_content.py             # 📥 Pobieranie
├── transcribe_podcast.py           # 🎙️ Transkrypcja
├── analyze_virals.py               # 📊 Analiza
├── cutter.py                       # ✂️ Wycinanie
└── requirements.txt
```

---

## 🚀 Quick Start

### 1. Instalacja
```bash
pip install -r requirements.txt
```

### 2. Konfiguracja
```bash
# .env (jeśli potrzebny Google API)
export GOOGLE_API_KEY="your-key-here"
```

### 3. Uruchomienie
```bash
python manager.py --url "https://www.youtube.com/watch?v=..." --cleanup
```

### 4. Wyniki
- Gotowe wycinki z napisami: `cuts/subtitles/`
- Każdy plik to 30-60s Shorts-ready video

---

## 🧪 Testowanie

Przetestowano na:
- ✅ 3 wycięte segmenty
- ✅ 5 surowych wideo (raw) + 5 z napisami
- ✅ Dynamiczne napisy (1-3 słowa)
- ✅ Workflow complete (pobieranie → subtitles)
- ✅ Error handling i retry logic
- ✅ Folder auto-creation

---

## 📊 Wyniki Testu

```
✓ WORKFLOW UKOŃCZONY

📊 Wygenerowane pliki:

  📂 Surowe wycinki (3 plików):
    - segment_1_08-57_53_09-31_26.mp4
    - segment_2_19-03_10_19-33_66.mp4
    - segment_3_00-15_04_00-51_00.mp4

  📂 Wycinki z napisami (3 plików):
    - segment_1_08-57_53_09-31_26.mp4
    - segment_2_19-03_10_19-33_66.mp4
    - segment_3_00-15_04_00-51_00.mp4

  📁 Katalog output: cuts/subtitles/
```

---

## 🔧 Troubleshooting

### Błąd: `ffmpeg not found`
```bash
# Ubuntu/Debian
sudo apt-get install ffmpeg

# macOS
brew install ffmpeg
```

### Błąd: `GOOGLE_API_KEY not set`
```bash
# Ustaw zmienną środowiska
export GOOGLE_API_KEY="your-key"
```

### Błąd transkrypcji
- Manager automatycznie spróbuje 2 razy
- Sprawdź limit API w Google Cloud

### Wideo bez napisów
- Sprawdź czy transkrypcja istnieje: `transcripts/Naruciak_Final.json`
- Sprawdź czy heatmapa istnieje: `metadata/heatmap.json`

---

## 📝 Notatki

- Napisy są **wbijane bezpośrednio** (burnt-in) za pomocą filtru ffmpeg `subtitles`
- Tekst: dynamiczny (1-3 słowa), duża czcionka, czytelny
- Safe zones: dolna 1/3 ekranu, MarginV=80 (nie zasłania UI)
- Wszystkie procesy logują output dla debugowania
- Workflow jest modularny - każdy krok można uruchomić niezależnie

---

## 🎬 Przyszłe Ulepszenia

- [ ] Support dla innych języków (OCR/TTS)
- [ ] Automatyczne testy
- [ ] GUI/WebUI dla managera
- [ ] Batch processing (wiele wideo na raz)
- [ ] Custom fontki/kolory dla napisów
- [ ] Export do TikTok/YouTube Shorts API
