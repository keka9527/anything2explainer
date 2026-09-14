"""Volcengine Seed TTS 2.0 SSE adapter and local subtitle alignment.

The wire format follows ByteDance's Apache-2.0 AgentKit TTS sample, while this
implementation is purpose-built for anything2explainer. Credentials are read
from the environment only and are never written to cache or timeline files.
"""
import base64
import difflib
import json
import os
import re
import uuid


ENDPOINT = 'https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse'


def _event_payload(line):
    if not line.startswith('data:'):
        return None
    try:
        return json.loads(line[5:].strip())
    except json.JSONDecodeError:
        return None


async def synthesize(text, api_key, speaker, resource_id='seed-tts-2.0', app_id='',
                     speech_rate=0, loudness_rate=0, pitch=0, sample_rate=24000):
    if not api_key:
        raise RuntimeError('缺少火山凭据：新版填写 VOLCENGINE_TTS_API_KEY；旧应用填写 VOLCENGINE_TTS_ACCESS_KEY')
    if not -50 <= speech_rate <= 100:
        raise ValueError('VOLCENGINE_TTS_SPEECH_RATE 必须在 -50 到 100')
    if not -50 <= loudness_rate <= 100 or not -12 <= pitch <= 12:
        raise ValueError('音量范围 -50..100，音调范围 -12..12')
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError('火山 TTS 需要 httpx：pip install httpx') from exc
    body = {
        'user': {'uid': 'anything2explainer'},
        'req_params': {
            'text': text,
            'speaker': speaker,
            'sample_rate': sample_rate,
            'audio_params': {
                'format': 'mp3', 'speech_rate': speech_rate,
                'loudness_rate': loudness_rate, 'bit_rate': 64000,
            },
            'additions': json.dumps({
                'post_process': {'pitch': pitch},
                'disable_markdown_filter': True,
                'enable_latex_tn': False,
                'latex_parser': 'v2',
            }),
        },
    }
    headers = {
        'Content-Type': 'application/json',
        'X-Api-Resource-Id': resource_id,
        'X-Api-Request-Id': str(uuid.uuid4()),
    }
    if app_id:
        headers['X-Api-App-Id'] = app_id
        headers['X-Api-Access-Key'] = api_key
    else:
        headers['X-Api-Key'] = api_key
    audio = bytearray()
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream('POST', ENDPOINT, headers=headers, json=body) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                event = _event_payload(line)
                if not event:
                    continue
                code = event.get('code', 0)
                if code not in (0, 20000000):
                    raise RuntimeError(f"火山 TTS 返回错误 {code}: {event.get('message', event)}")
                encoded = event.get('data')
                if encoded:
                    audio.extend(base64.b64decode(encoded))
    if not audio:
        raise RuntimeError('火山 TTS 没有返回音频')
    return bytes(audio)


def _plain(text):
    return ''.join(re.findall(r'[\w\u3400-\u9fff]+', text, re.UNICODE)).lower()


_model = None


def align_chunks(audio_path, chunks, model_name='base', language='zh'):
    """Return one start second per subtitle chunk using local faster-whisper."""
    global _model
    targets = [_plain(c) for c in chunks]
    target_text = ''.join(targets)
    fallback = [0.0]
    if _model is None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError('自然段字幕对齐需要 faster-whisper：pip install faster-whisper') from exc
        _model = WhisperModel(model_name, device='cpu', compute_type='int8')
    segments, _ = _model.transcribe(audio_path, language=language, word_timestamps=True,
                                    beam_size=5, vad_filter=False)
    heard = []
    heard_times = []
    end_time = 0.0
    for seg in segments:
        end_time = max(end_time, float(seg.end))
        for word in seg.words or []:
            token = _plain(word.word)
            if not token:
                continue
            span = max(0.01, float(word.end) - float(word.start))
            for index, char in enumerate(token):
                heard.append(char)
                heard_times.append(float(word.start) + span * index / len(token))
    if not heard_times or not target_text:
        total = max(end_time, 0.1)
        return [total * sum(len(x) for x in targets[:i]) / max(1, len(target_text)) for i in range(len(targets))]
    mapping = {}
    matcher = difflib.SequenceMatcher(None, target_text, ''.join(heard), autojunk=False)
    for a, b, size in matcher.get_matching_blocks():
        for offset in range(size):
            mapping[a + offset] = heard_times[b + offset]
    known = sorted(mapping)
    def time_at(pos):
        if pos in mapping:
            return mapping[pos]
        left = next((k for k in reversed(known) if k < pos), None)
        right = next((k for k in known if k > pos), None)
        if left is not None and right is not None:
            ratio = (pos - left) / (right - left)
            return mapping[left] + ratio * (mapping[right] - mapping[left])
        if left is not None:
            return min(end_time, mapping[left] + (pos - left) * 0.12)
        if right is not None:
            return max(0.0, mapping[right] - (right - pos) * 0.12)
        return end_time * pos / max(1, len(target_text))
    starts = []
    cursor = 0
    for target in targets:
        starts.append(time_at(cursor))
        cursor += len(target)
    starts[0] = 0.0
    return starts
