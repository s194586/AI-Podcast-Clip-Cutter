import argparse
import json
import os
import re
import time
from bisect import bisect_left
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

try:
    import google.generativeai as genai
except Exception:
    try:
        import google.genai as genai
    except Exception:
        genai = None

SENTENCE_BREAK_RE = re.compile(r'(?<=[\.!?…])\s+')
RETRY_DELAYS_SECONDS = (5, 10, 20)


def is_rate_limit_error(exc):
    parts = [
        str(exc),
        str(getattr(exc, 'code', '')),
        str(getattr(exc, 'status', '')),
        str(getattr(exc, 'reason', '')),
    ]
    message = ' '.join(parts).lower()
    return (
        '429' in message
        or 'too many requests' in message
        or 'rate limit' in message
        or 'resource_exhausted' in message
        or 'quota' in message
    )


def wait_before_retry(exc, attempt, max_retries, operation):
    if attempt >= max_retries:
        return
    delay = RETRY_DELAYS_SECONDS[min(attempt - 1, len(RETRY_DELAYS_SECONDS) - 1)]
    reason = '429/rate limit' if is_rate_limit_error(exc) else 'temporary API error'
    print(f'  ⚠ {operation}: {reason}, retry za {delay}s ({attempt}/{max_retries})')
    time.sleep(delay)


def configure_gemini(api_key):
    if genai is None:
        raise RuntimeError('google-generativeai nie jest zainstalowane.')
    if hasattr(genai, 'configure'):
        genai.configure(api_key=api_key)


def generate_content_with_backoff(model, payload, operation, max_retries=3):
    for attempt in range(1, max_retries + 1):
        try:
            return model.generate_content(payload)
        except Exception as exc:
            if attempt == max_retries:
                raise
            wait_before_retry(exc, attempt, max_retries, operation)
    raise RuntimeError(f'Gemini call failed: {operation}')


def extract_json_object(text):
    if not text:
        raise ValueError('Pusta odpowiedź modelu.')
    cleaned = re.sub(r'```(?:json)?\s*(.*?)\s*```', r'\1', text, flags=re.S).strip()
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    start = cleaned.find('{')
    end = cleaned.rfind('}')
    if start != -1 and end != -1 and end > start:
        candidate = re.sub(r',\s*([}\]])', r'\1', cleaned[start:end + 1])
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    raise ValueError(f'Nie udało się wyciągnąć JSON z odpowiedzi modelu: {text[:200]}')


def parse_time(time_str):
    if isinstance(time_str, (int, float)):
        return float(time_str)
    parts = [p for p in time_str.split(':') if p != '']
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds)
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    raise ValueError(f'Niepoprawny format czasu: {time_str}')


def load_transcript(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        transcript = json.load(f)
    if isinstance(transcript, dict) and 'segments' in transcript:
        transcript = transcript['segments']
    return transcript


def load_heatmap(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def split_sentences(text):
    parts = SENTENCE_BREAK_RE.split(text.strip())
    sentences = [part.strip() for part in parts if part.strip()]
    return sentences or [text.strip()]


def build_sentence_boundaries(transcript):
    sentences = []
    for segment in transcript:
        start = parse_time(segment['start'])
        end = parse_time(segment['end'])
        text = segment.get('text', '').replace('\n', ' ').strip()
        speaker = segment.get('speaker') or segment.get('speaker_id') or segment.get('speakerId')
        if not text:
            continue

        pieces = split_sentences(text)
        if len(pieces) == 1:
            sentences.append({'start': start, 'end': end, 'text': pieces[0], 'speaker': speaker})
            continue

        total_chars = sum(len(piece) for piece in pieces)
        if total_chars == 0:
            sentences.append({'start': start, 'end': end, 'text': text, 'speaker': speaker})
            continue

        cursor = start
        consumed = 0
        for piece in pieces[:-1]:
            consumed += len(piece)
            portion = consumed / total_chars
            boundary = start + (end - start) * portion
            sentences.append({'start': cursor, 'end': boundary, 'text': piece, 'speaker': speaker})
            cursor = boundary
        sentences.append({'start': cursor, 'end': end, 'text': pieces[-1], 'speaker': speaker})
    return sentences


def build_heatmap_index(heatmap):
    heatmap_sorted = sorted(heatmap, key=lambda entry: entry['start_time'])
    starts = [entry['start_time'] for entry in heatmap_sorted]
    return heatmap_sorted, starts


def average_heatmap_value(heatmap, starts, window_start, window_end):
    idx = bisect_left(starts, window_start)
    if idx > 0:
        idx -= 1

    total_weight = 0.0
    weighted_sum = 0.0
    for entry in heatmap[idx:]:
        entry_start = entry['start_time']
        entry_end = entry['end_time']
        if entry_start >= window_end:
            break
        overlap_start = max(window_start, entry_start)
        overlap_end = min(window_end, entry_end)
        overlap = overlap_end - overlap_start
        if overlap <= 0:
            continue
        total_weight += overlap
        weighted_sum += overlap * entry['value']
    if total_weight == 0:
        return 0.0
    return weighted_sum / total_weight


def collect_text_for_window(sentences, window_start, window_end):
    parts = []
    for sentence in sentences:
        if sentence['end'] <= window_start:
            continue
        if sentence['start'] >= window_end:
            break
        parts.append(sentence['text'])
    return ' '.join(parts).strip()


def summarize_text(text, max_chars=220):
    summary = ' '.join(text.split())
    if len(summary) <= max_chars:
        return summary
    truncated = summary[:max_chars].rstrip()
    if '.' in truncated:
        truncated = truncated[: truncated.rfind('.') + 1]
    if len(truncated) < 40:
        truncated = summary[:max_chars].rstrip()
    return truncated + '…'


def collect_context(sentences, window_start, window_end, margin=20.0):
    context_start = max(0.0, window_start - margin)
    context_end = window_end + margin
    context = []
    for sentence in sentences:
        if sentence['end'] <= context_start:
            continue
        if sentence['start'] >= context_end:
            break
        speaker = sentence.get('speaker')
        label = f' [{speaker}]' if speaker else ''
        context.append({
            'start': sentence['start'],
            'end': sentence['end'],
            'text': sentence['text'],
            'speaker': speaker,
            'line': f'{format_time(sentence["start"])} - {format_time(sentence["end"])}{label}: {sentence["text"]}',
        })
    return context


def clamp(value, lower, upper):
    return max(lower, min(upper, value))


def boundary_start_for_time(sentences, value):
    for sentence in sentences:
        if sentence['start'] <= value < sentence['end']:
            return sentence['start']
    starts = [sentence['start'] for sentence in sentences]
    if not starts:
        return value
    return min(starts, key=lambda item: abs(item - value))


def boundary_end_for_time(sentences, value):
    for sentence in sentences:
        if sentence['start'] < value <= sentence['end']:
            return sentence['end']
    ends = [sentence['end'] for sentence in sentences]
    if not ends:
        return value
    return min(ends, key=lambda item: abs(item - value))


def enforce_story_bounds(start, end, context, fallback, max_duration):
    if not context:
        return fallback['start'], fallback['end'], ['Brak kontekstu transkrypcji, zachowano okno heatmapy.']

    context_start = context[0]['start']
    context_end = context[-1]['end']
    adjusted_start = boundary_start_for_time(context, clamp(start, context_start, context_end))
    adjusted_end = boundary_end_for_time(context, clamp(end, context_start, context_end))
    decisions = []

    if adjusted_start != start:
        decisions.append(f'Przesunięto hook do początku zdania: {format_time(adjusted_start)}.')
    if adjusted_end != end:
        decisions.append(f'Przesunięto puentę do końca zdania: {format_time(adjusted_end)}.')

    if adjusted_end <= adjusted_start:
        adjusted_start = fallback['start']
        adjusted_end = fallback['end']
        decisions.append('AI zwróciło nielogiczne granice, użyto okna heatmapy.')

    if adjusted_end - adjusted_start > max_duration:
        limit = adjusted_start + max_duration
        safe_ends = [item['end'] for item in context if adjusted_start < item['end'] <= limit]
        if safe_ends:
            old_end = adjusted_end
            adjusted_end = max(safe_ends)
            decisions.append(
                f'Skrócono puentę z {format_time(old_end)} do {format_time(adjusted_end)}, aby zmieścić limit {max_duration:.0f}s.'
            )
        else:
            old_start = adjusted_start
            adjusted_start = max(context_start, adjusted_end - max_duration)
            adjusted_start = boundary_start_for_time(context, adjusted_start)
            decisions.append(
                f'Przesunięto hook z {format_time(old_start)} do {format_time(adjusted_start)}, aby zmieścić limit {max_duration:.0f}s.'
            )

    return adjusted_start, adjusted_end, decisions


def refine_window_with_ai(window, context, model_name, max_duration):
    context_text = '\n'.join(item['line'] for item in context)
    prompt = (
        'Jesteś montażystą krótkich filmów. Masz wybrać zamkniętą historię do Shorts.\n'
        'Na podstawie transkrypcji z marginesem +/-20s znajdź idealny Hook (start) i Puentę (koniec).\n'
        'Nie zaczynaj ani nie kończ w połowie zdania. Koniec może wyjść poza pierwotny limit heatmapy, '
        f'ale cały klip musi mieć maksymalnie {max_duration:.0f}s.\n\n'
        f'PIERWOTNE_OKNO_HEATMAPY: {format_time(window["start"])} - {format_time(window["end"])}\n'
        f'KONTEKST:\n{context_text}\n\n'
        'Zwróć WYŁĄCZNIE JSON jako obiekt z kluczami: '
        '"hook_start" string timestamp MM:SS.ss, "punchline_end" string timestamp MM:SS.ss, '
        '"reason" string, "hook_reason" string, "ending_reason" string.'
    )
    model = genai.GenerativeModel(model_name)
    result = generate_content_with_backoff(model, [prompt], 'Gemini smart context cutter')
    text = getattr(result, 'text', None)
    if not text and hasattr(result, 'candidates') and result.candidates:
        text = str(result.candidates[0])
    parsed = extract_json_object(text or '')
    return {
        'hook_start': parse_time(parsed.get('hook_start', window['start'])),
        'punchline_end': parse_time(parsed.get('punchline_end', window['end'])),
        'reason': str(parsed.get('reason', '')).strip(),
        'hook_reason': str(parsed.get('hook_reason', '')).strip(),
        'ending_reason': str(parsed.get('ending_reason', '')).strip(),
    }


def smart_refine_windows(windows, sentences, *, model_name, api_key, max_duration, context_margin, log_path):
    log = {
        'model': model_name,
        'context_margin_seconds': context_margin,
        'max_duration_seconds': max_duration,
        'decisions': [],
    }
    if not windows:
        return windows, log
    if genai is None or not api_key:
        log['status'] = 'skipped'
        log['reason'] = 'Brak google-generativeai lub klucza API, użyto okien heatmapy.'
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, 'w', encoding='utf-8') as f:
            json.dump(log, f, ensure_ascii=False, indent=2)
        return windows, log

    configure_gemini(api_key)
    refined = []
    for index, window in enumerate(windows, start=1):
        context = collect_context(sentences, window['start'], window['end'], margin=context_margin)
        decision = {
            'index': index,
            'heatmap_start': window['start'],
            'heatmap_end': window['end'],
            'heatmap_start_label': format_time(window['start']),
            'heatmap_end_label': format_time(window['end']),
            'context_start_label': format_time(context[0]['start']) if context else None,
            'context_end_label': format_time(context[-1]['end']) if context else None,
        }
        try:
            ai_choice = refine_window_with_ai(window, context, model_name, max_duration)
            start, end, adjustments = enforce_story_bounds(
                ai_choice['hook_start'],
                ai_choice['punchline_end'],
                context,
                window,
                max_duration,
            )
            refined_window = dict(window)
            refined_window.update({
                'heatmap_start': window['start'],
                'heatmap_end': window['end'],
                'start': start,
                'end': end,
                'duration': end - start,
                'summary': summarize_text(collect_text_for_window(sentences, start, end)),
                'text': collect_text_for_window(sentences, start, end),
                'ai_reason': ai_choice['reason'],
                'hook_reason': ai_choice['hook_reason'],
                'ending_reason': ai_choice['ending_reason'],
                'smart_context': True,
            })
            decision.update({
                'status': 'refined',
                'ai_hook_start': ai_choice['hook_start'],
                'ai_punchline_end': ai_choice['punchline_end'],
                'final_start': start,
                'final_end': end,
                'final_start_label': format_time(start),
                'final_end_label': format_time(end),
                'final_duration': end - start,
                'reason': ai_choice['reason'],
                'hook_reason': ai_choice['hook_reason'],
                'ending_reason': ai_choice['ending_reason'],
                'adjustments': adjustments,
            })
            refined.append(refined_window)
        except Exception as exc:
            fallback = dict(window)
            fallback['smart_context'] = False
            fallback['ai_error'] = str(exc)
            decision.update({'status': 'fallback', 'error': str(exc)})
            refined.append(fallback)
        log['decisions'].append(decision)

    log['status'] = 'checked'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, 'w', encoding='utf-8') as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
    return refined, log


def build_candidates(sentences, heatmap, starts, min_duration, max_duration):
    sentence_boundaries = sorted({boundary for s in sentences for boundary in (s['start'], s['end'])})
    candidates = []

    for window_start in sentence_boundaries:
        min_end = window_start + min_duration
        max_end = window_start + max_duration
        valid_ends = [boundary for boundary in sentence_boundaries if boundary >= min_end and boundary <= max_end]
        if not valid_ends:
            continue

        for window_end in valid_ends:
            avg_value = average_heatmap_value(heatmap, starts, window_start, window_end)
            if avg_value <= 0:
                continue
            text_snippet = collect_text_for_window(sentences, window_start, window_end)
            candidates.append({
                'start': window_start,
                'end': window_end,
                'duration': window_end - window_start,
                'avg_value': avg_value,
                'summary': summarize_text(text_snippet),
                'text': text_snippet,
            })
    return candidates


def select_non_overlapping(candidates, count=3):
    selected = []
    for candidate in sorted(candidates, key=lambda x: x['avg_value'], reverse=True):
        if any(not (candidate['end'] <= chosen['start'] or candidate['start'] >= chosen['end']) for chosen in selected):
            continue
        selected.append(candidate)
        if len(selected) == count:
            break
    return selected


def format_time(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    if hours > 0:
        return f'{hours:d}:{minutes:02d}:{secs:05.2f}'
    return f'{minutes:02d}:{secs:05.2f}'


def save_top_windows(windows, output_path):
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(windows, f, ensure_ascii=False, indent=2)


def parse_args():
    parser = argparse.ArgumentParser(description='Analiza viralowych fragmentów z heatmapy i transkrypcji')
    parser.add_argument('--transcript', default='transcripts/Naruciak_Final.json', help='Ścieżka do transkrypcji JSON')
    parser.add_argument('--heatmap', default='metadata/heatmap.json', help='Ścieżka do heatmapy JSON')
    parser.add_argument('--min-duration', type=float, default=30.0, help='Minimalna długość okna w sekundach')
    parser.add_argument('--max-duration', type=float, default=60.0, help='Maksymalna długość okna w sekundach')
    parser.add_argument('--top', type=int, default=3, help='Liczba najlepszych momentów do wypisania')
    parser.add_argument('--save-json', default=None, help='Zapisz wybrane okna do pliku JSON')
    parser.add_argument('--model', default='models/gemini-2.5-flash', help='Model Gemini do Smart Context Cutter')
    parser.add_argument('--context-margin', type=float, default=20.0, help='Margines transkrypcji dla AI po obu stronach okna')
    parser.add_argument('--cutting-log', default='metadata/cutting_logic.json', help='Log decyzji Smart Context Cutter')
    parser.add_argument('--skip-smart-context', action='store_true', help='Pomiń AI i użyj samych okien z heatmapy')
    return parser.parse_args()


def main():
    args = parse_args()

    if load_dotenv is not None:
        dotenv_path = Path(__file__).parent / '.env'
        if dotenv_path.exists():
            load_dotenv(dotenv_path)

    transcript = load_transcript(args.transcript)
    heatmap = load_heatmap(args.heatmap)
    sentences = build_sentence_boundaries(transcript)
    heatmap_index, heatmap_starts = build_heatmap_index(heatmap)

    candidates = build_candidates(sentences, heatmap_index, heatmap_starts, args.min_duration, args.max_duration)
    if not candidates:
        raise SystemExit('Nie znaleziono żadnych okien spełniających kryteria 30-60 sekund.')

    top_windows = select_non_overlapping(candidates, count=args.top)
    if not top_windows:
        raise SystemExit('Nie udało się wybrać niepokrywających się okien. Spróbuj zmniejszyć liczbę top lub dopasować parametry.')

    if not args.skip_smart_context:
        api_key = os.environ.get('GOOGLE_API_KEY') or os.environ.get('GEMINI_API_KEY') or os.environ.get('API_KEY')
        top_windows, cutting_log = smart_refine_windows(
            top_windows,
            sentences,
            model_name=args.model,
            api_key=api_key,
            max_duration=args.max_duration,
            context_margin=args.context_margin,
            log_path=Path(args.cutting_log),
        )
        if cutting_log.get('status') == 'skipped':
            print(f'  ⚠ Smart Context Cutter pominięty: {cutting_log.get("reason")}')
    else:
        log_path = Path(args.cutting_log)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, 'w', encoding='utf-8') as f:
            json.dump({
                'status': 'skipped',
                'reason': 'Użyto --skip-smart-context.',
                'decisions': [],
            }, f, ensure_ascii=False, indent=2)

    print('\nTop {} momentów do Shortsów:'.format(len(top_windows)))
    for index, window in enumerate(top_windows, start=1):
        print(f'Nr {index}:')
        print(f'  Zakres: {format_time(window["start"])} - {format_time(window["end"])}')
        print(f'  Średni wynik heatmapy: {window["avg_value"]:.4f}')
        print(f'  Długość: {window["duration"]:.1f}s')
        print(f'  Opis: {window["summary"]}')
        print()

    if args.save_json:
        out_path = Path(args.save_json)
        save_top_windows(top_windows, out_path)
        print(f'Zapisano wybrane okna do: {out_path}')


if __name__ == '__main__':
    main()
